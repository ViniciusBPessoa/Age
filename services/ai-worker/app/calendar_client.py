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
            case _:
                raise ValueError(f"operação desconhecida: {operation}")

    async def _get(self, path: str, params: dict) -> Any:
        url = f"{self._base}/internal/calendar{path}"
        headers = {
            "Authorization": f"Bearer {_service_token()}",
            "X-Age-User-Id": str(self._user_id),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params=params, headers=headers)

        if response.status_code >= 400:
            raise RuntimeError(
                f"core-api recusou leitura de agenda ({response.status_code})"
            )
        return response.json()

    async def list_events(self, start: str, end: str) -> list[dict]:
        data = await self._get("/events", {"start": start, "end": end})
        return data["events"]

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
        headers = {
            "Authorization": f"Bearer {_service_token()}",
            "X-Age-User-Id": str(self._user_id),
        }
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
        headers = {
            "Authorization": f"Bearer {_service_token()}",
            "X-Age-User-Id": str(self._user_id),
        }
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
