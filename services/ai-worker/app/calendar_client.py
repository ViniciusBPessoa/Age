"""Acesso à agenda pelo worker.

ARQUITETURA.md §3.4 explica a assimetria: a porta `CalendarProvider`
vive no `core-api`, porque escrita em agenda é dele. O worker **lê** a
agenda (precisa, para montar contexto e buscar slots) mas nunca escreve.

Duas formas de o worker ler, e a escolha importa:

- **Direto no banco** seria mais rápido, mas duplicaria o Scheduler e a
  lógica de unificar calendário nativo com externos. Duas
  implementações da mesma regra divergem.
- **HTTP interno ao core-api** custa um salto de ~1 ms e mantém uma
  implementação só.

Escolhi HTTP. O Scheduler é regra de negócio delicada (DST, fusos,
limites); ter duas versões dela seria o pior tipo de duplicação.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import jwt

from app.config import get_settings
from app.tracing import HEADER as TRACE_HEADER
from app.tracing import current_trace_id

log = logging.getLogger("ai-worker.calendar")


def _service_token() -> str:
    """JWT de serviço, vida de 60 s (§2.3.1)."""
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "ai-worker",
            "aud": "core-api",
            "typ": "service",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=60)).timestamp()),
        },
        settings.service_jwt_secret,
        algorithm="HS256",
    )


class CalendarClient:
    """Cliente de leitura de agenda, por usuário."""

    def __init__(self, user_id: UUID, *, base_url: str | None = None) -> None:
        self._user_id = user_id
        settings = get_settings()
        self._base = (base_url or settings.core_api_internal_url).rstrip("/")

    async def __call__(self, operation: str, **kwargs: Any) -> Any:
        """Despacho por nome, para casar com o callback das tools."""
        match operation:
            case "list_events":
                return await self.list_events(kwargs["start"], kwargs["end"])
            case "pending_proposals":
                return await self.pending_proposals(
                    kwargs["start"], kwargs["end"]
                )
            case "find_free_slots":
                return await self.find_free_slots(**kwargs)
            case "summarize_workload":
                return await self.summarize_workload(
                    kwargs["start"], kwargs["end"]
                )
            case "build_study_plan":
                return await self.build_study_plan(**kwargs)
            case "study_progress":
                return await self.study_progress(kwargs.get("subject"))
            case "mark_session":
                return await self.mark_session(**kwargs)
            case "save_preference":
                return await self.save_preference(**kwargs)
            case "task_overview":
                return await self.task_overview(
                    kwargs.get("horizon_days", 7)
                )
            case "list_tasks":
                return await self.list_tasks(**kwargs)
            case "get_task_lists":
                return await self.get_task_lists()
            case "list_routines":
                return await self.list_routines()
            case "routine_adherence":
                return await self.routine_adherence(kwargs.get("days", 28))
            case "period_report":
                return await self.period_report(kwargs.get("days", 7))
            case "external_changes":
                return await self.external_changes()
            case "mark_routine":
                return await self.mark_routine(**kwargs)
            case _:
                raise ValueError(f"operação desconhecida: {operation}")

    def _headers(self) -> dict[str, str]:
        """Headers de toda chamada interna, num lugar só.

        Eram três dicionários idênticos, um por método de chamada, e a
        duplicação deixou de ser inofensiva quando o `X-Trace-Id`
        entrou: esquecer o header num dos três quebraria o trace apenas
        nas tools que passam por aquele caminho — o tipo de furo que só
        aparece quando se está depurando outra coisa.

        O trace sai do contexto do job (`tracing.bind`, na entrada de
        `run_turn`), e é o mesmo que o navegador gerou. É o salto que
        fechava o círculo: worker → core-api começava um trace novo, e
        as duas metades da mesma operação ficavam sem ligação.
        """
        headers = {
            "Authorization": f"Bearer {_service_token()}",
            "X-Age-User-Id": str(self._user_id),
        }
        trace = current_trace_id()
        if trace:
            headers[TRACE_HEADER] = trace
        return headers

    async def _get(self, path: str, params: dict) -> Any:
        return await self._get_at("/internal/calendar", path, params)

    async def _get_at(self, prefix: str, path: str, params: dict) -> Any:
        """GET numa superfície interna do core-api.

        O prefixo é parâmetro porque o worker lê de duas superfícies
        (`/internal/calendar` e `/internal/tasks`) e nenhuma das duas
        permite escrita — escrever passa pelo `PlanExecutor` (§3.4).
        """
        url = f"{self._base}{prefix}{path}"
        headers = self._headers()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params=params, headers=headers)

        if response.status_code >= 400:
            raise RuntimeError(
                f"core-api recusou leitura em {prefix} "
                f"({response.status_code})"
            )
        return response.json()

    async def list_events(self, start: str, end: str) -> list[dict]:
        data = await self._get("/events", {"start": start, "end": end})
        return data["events"]

    async def pending_proposals(self, start: str, end: str) -> dict:
        """Propostas pendentes na janela.

        Separada de `list_events` de propósito: propostos NÃO contam
        como ocupação para o Scheduler (§5.3). Ver a docstring da rota
        no core-api para o relato que originou isto.
        """
        return await self._get(
            "/pending-proposals", {"start": start, "end": end}
        )

    async def find_free_slots(
        self,
        *,
        start: str,
        end: str,
        duration_min: int,
        deadline: str | None = None,
        count: int = 10,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "duration_min": duration_min,
            "count": count,
        }
        if deadline:
            params["deadline"] = deadline
        data = await self._get("/free-slots", params)
        return data["slots"]

    async def summarize_workload(self, start: str, end: str) -> list[dict]:
        data = await self._get("/workload", {"start": start, "end": end})
        return data["days"]

    # -----------------------------------------------------------
    # Estudo (§5.5). O Planner vive no core-api: duplicá-lo aqui
    # criaria duas versões de uma regra delicada (pré-requisitos,
    # espaçamento, DST), que divergiriam.
    # -----------------------------------------------------------
    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base}{path}"
        headers = self._headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise RuntimeError(
                f"core-api recusou {path} ({response.status_code})"
            )
        return response.json()

    async def build_study_plan(
        self,
        *,
        subject: str,
        topics: list[dict],
        deadline: str | None = None,
        start_from: str | None = None,
        include_reviews: bool = True,
    ) -> dict:
        return await self._post(
            "/internal/study/plan",
            {
                "subject": subject,
                "topics": topics,
                "deadline": deadline,
                "start_from": start_from,
                "include_reviews": include_reviews,
            },
        )

    async def study_progress(self, subject: str | None = None) -> dict:
        params = {"subject": subject} if subject else {}
        url = f"{self._base}/internal/study/progress"
        headers = self._headers()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params=params, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"core-api recusou progresso ({response.status_code})"
            )
        return response.json()

    async def mark_session(
        self,
        *,
        session_id: str,
        status: str,
        actual_min: int | None = None,
        difficulty: int | None = None,
    ) -> dict:
        return await self._post(
            "/internal/study/mark",
            {
                "session_id": session_id,
                "status": status,
                "actual_min": actual_min,
                "difficulty": difficulty,
            },
        )

    async def save_preference(
        self, *, key: str, value: object, confirmed: bool = False
    ) -> dict:
        return await self._post(
            "/internal/study/preference",
            {"key": key, "value": value, "confirmed": confirmed},
        )

    # -----------------------------------------------------------
    # Tarefas (§5.9.5) — somente leitura
    # -----------------------------------------------------------
    async def task_overview(self, horizon_days: int = 7) -> dict:
        """Panorama da lista, com os números já calculados.

        O modelo não soma nem conta: recebe isto pronto. Mesma decisão
        do `find_free_slots` — o LLM não inventa tempo livre, e também
        não inventa quantas tarefas vencem nem quais têm bloco
        reservado.
        """
        return await self._get_at(
            "/internal/tasks", "/overview", {"horizon_days": horizon_days}
        )

    async def list_tasks(
        self,
        include_done: bool = False,
        due_before: str | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {"include_done": include_done}
        if due_before:
            params["due_before"] = due_before
        data = await self._get_at("/internal/tasks", "/list", params)
        return data["tasks"]

    async def get_task_lists(self) -> list[dict]:
        data = await self._get_at("/internal/tasks", "/lists", {})
        return data["lists"]

    # -----------------------------------------------------------
    # Rotinas, relatório e mudança externa (§5.9.4, §5.9.9, §5.10)
    # -----------------------------------------------------------
    async def list_routines(self) -> list[dict]:
        data = await self._get_at("/internal/routines", "/list", {})
        return data["routines"]

    async def routine_adherence(self, days: int = 28) -> list[dict]:
        data = await self._get_at(
            "/internal/routines", "/adherence", {"days": days}
        )
        return data["adherence"]

    async def period_report(self, days: int = 7) -> dict:
        """O relatório de §5.9.9, com números e padrões calculados."""
        return await self._get_at(
            "/internal/routines", "/report", {"days": days}
        )

    async def external_changes(self) -> dict:
        """O que mudou fora do Age e ainda não foi reconhecido (§5.10).

        Note que o worker LÊ e não reconhece: marcar como visto é da
        rota pública, chamada pelo front quando o usuário de fato abre
        o chat. Reconhecer aqui faria a mudança virar "lida" sem
        ninguém ter lido.
        """
        return await self._get_at(
            "/internal/routines", "/external-changes", {}
        )

    async def mark_routine(
        self,
        routine_id: str,
        scheduled_start: str,
        status: str,
        actual_min: int | None = None,
        moved_to: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Registro de fato (§5.2): executa, não propõe."""
        # `_post` já recebe o caminho completo, então não há `_post_at`:
        # o prefixo entra aqui mesmo.
        return await self._post(
            f"/internal/routines/{routine_id}/mark",
            {
                "scheduled_start": scheduled_start,
                "status": status,
                "actual_min": actual_min,
                "moved_to": moved_to,
                "notes": notes,
            },
        )
