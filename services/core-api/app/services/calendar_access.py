"""Acesso unificado à agenda do usuário.

ARQUITETURA.md §3.2.1 (modo híbrido) e §6.3. Esta camada resolve uma
coisa que o `Scheduler` não deve saber: de onde vem cada intervalo
ocupado.

O usuário pode ter calendário nativo, Google, ou os dois ao mesmo tempo.
O Scheduler recebe os intervalos já unificados — é por isso que ele é
uma função pura de lista de `BusyInterval`, e não precisa de nenhum
conceito de provedor.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.calendar.local import LocalCalendar
from app.domain.scheduler import (
    DailyWindow,
    SchedulingPolicy,
    ScoringWeights,
)
from app.ports.calendar import BusyInterval, Event, Flexibility

log = logging.getLogger("core-api.calendar_access")

# Janela máxima de consulta (§7.0): sem limite, uma chamada sem
# parâmetro varreria anos.
MAX_WINDOW = timedelta(days=90)


async def load_policy(db: AsyncSession, user_id: UUID) -> SchedulingPolicy:
    """Monta a política a partir das preferências do usuário (§6.1)."""
    row = (
        await db.execute(
            text(
                """
                SELECT u.timezone, p.working_hours, p.blocked_windows,
                       p.preferred_study_times, p.session_min, p.session_max,
                       p.break_min, p.max_hours_per_day, p.buffer_min,
                       p.scoring_weights
                FROM core.users u
                LEFT JOIN core.user_preferences p ON p.user_id = u.id
                WHERE u.id = :uid
                """
            ),
            {"uid": str(user_id)},
        )
    ).first()

    if row is None:
        return SchedulingPolicy()

    (
        timezone,
        working_hours,
        blocked_windows,
        preferred,
        session_min,
        session_max,
        break_min,
        max_hours,
        buffer_min,
        weights,
    ) = row

    return SchedulingPolicy(
        timezone=timezone or "America/Sao_Paulo",
        working_hours=_parse_windows(working_hours)
        or {d: (DailyWindow(time(8), time(22)),) for d in range(7)},
        blocked_windows=_parse_windows(blocked_windows) or {},
        preferred_hours=_parse_flat_windows(preferred),
        session_min=timedelta(minutes=session_min or 50),
        session_max=timedelta(minutes=session_max or 90),
        break_min=timedelta(minutes=break_min or 10),
        buffer=timedelta(minutes=buffer_min if buffer_min is not None else 15),
        max_hours_per_day=timedelta(hours=max_hours or 6),
        weights=_parse_weights(weights),
    )


def _parse_windows(raw) -> dict[int, tuple[DailyWindow, ...]]:
    """Converte `{"0": [["08:00","12:00"]]}` em janelas tipadas.

    Entrada malformada é ignorada em vez de derrubar a requisição: a
    preferência pode ter sido salva por versão antiga do front, e perder
    a agenda por causa disso seria desproporcional.
    """
    if not raw or not isinstance(raw, dict):
        return {}

    out: dict[int, tuple[DailyWindow, ...]] = {}
    for key, ranges in raw.items():
        try:
            weekday = int(key)
        except (TypeError, ValueError):
            continue
        if not 0 <= weekday <= 6 or not isinstance(ranges, list):
            continue

        windows = []
        for item in ranges:
            if not isinstance(item, list | tuple) or len(item) != 2:
                continue
            try:
                windows.append(
                    DailyWindow(
                        start=time.fromisoformat(item[0]),
                        end=time.fromisoformat(item[1]),
                    )
                )
            except (ValueError, TypeError):
                continue
        if windows:
            out[weekday] = tuple(windows)
    return out


def _parse_flat_windows(raw) -> tuple[DailyWindow, ...]:
    if not raw or not isinstance(raw, list):
        return ()
    out = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 2:
            continue
        try:
            out.append(
                DailyWindow(
                    start=time.fromisoformat(item[0]),
                    end=time.fromisoformat(item[1]),
                )
            )
        except (ValueError, TypeError):
            continue
    return tuple(out)


def _parse_weights(raw) -> ScoringWeights:
    if not raw or not isinstance(raw, dict):
        return ScoringWeights()
    allowed = {
        "deadline_proximity",
        "time_of_day",
        "contiguity",
        "daily_load",
        "spacing",
        "last_day_penalty",
    }
    clean = {
        k: float(v)
        for k, v in raw.items()
        if k in allowed and isinstance(v, int | float)
    }
    return ScoringWeights(**clean)


# ---------------------------------------------------------------
# Leitura unificada
# ---------------------------------------------------------------
async def read_events(
    db: AsyncSession,
    user_id: UUID,
    start: datetime,
    end: datetime,
    *,
    include_titles: bool = True,
) -> list[Event]:
    """Eventos de todas as fontes ativas do usuário.

    `include_titles=False` é o modo privado (§9.3): conta como ocupado
    sem expor o título.
    """
    _check_window(start, end)

    local = LocalCalendar(db=db, user_id=user_id)
    calendars = await local.list_calendars()
    events = await local.list_events(
        [c.id for c in calendars], start, end
    )

    # Eventos de provedores externos vêm da sombra, não de chamada ao
    # provedor: a sombra é o cache que o worker de sync mantém (§6.3).
    shadow = await _read_shadow(db, user_id, start, end)
    all_events = [*events, *shadow]

    if not include_titles:
        from dataclasses import replace

        all_events = [replace(e, title="(ocupado)") for e in all_events]

    return sorted(all_events, key=lambda e: e.start)


async def _read_shadow(
    db: AsyncSession, user_id: UUID, start: datetime, end: datetime
) -> list[Event]:
    rows = await db.execute(
        text(
            """
            SELECT s.external_event_id, s.external_calendar_id, s.title,
                   s.start_at, s.end_at, s.all_day, s.location,
                   s.recurrence, s.flexibility, s.created_by_age, s.etag,
                   sel.send_titles_to_llm
            FROM core.events_shadow s
            JOIN core.calendar_selections sel
              ON sel.connection_id = s.connection_id
             AND sel.external_calendar_id = s.external_calendar_id
            WHERE s.user_id = :uid
              AND s.deleted_at IS NULL
              AND sel.read_enabled = true
              AND s.start_at < :end AND s.end_at > :start
            ORDER BY s.start_at
            """
        ),
        {"uid": str(user_id), "start": start, "end": end},
    )

    out: list[Event] = []
    for r in rows.all():
        # Calendário marcado como "não enviar títulos" conta como
        # ocupado sem expor o que é (§9.3).
        title = r[2] if r[11] else "(ocupado)"
        out.append(
            Event(
                id=r[0],
                calendar_id=r[1],
                title=title or "(sem título)",
                start=r[3],
                end=r[4],
                all_day=r[5],
                location=r[6] if r[11] else None,
                recurrence=r[7],
                flexibility=Flexibility(r[8]),
                source="age" if r[9] else None,
                etag=r[10],
            )
        )
    return out


async def read_busy(
    db: AsyncSession, user_id: UUID, start: datetime, end: datetime
) -> list[BusyInterval]:
    """Intervalos ocupados, de todas as fontes.

    É o que o Scheduler consome. Caminho mais barato que `read_events`:
    não carrega título nem descrição.
    """
    _check_window(start, end)

    local = LocalCalendar(db=db, user_id=user_id)
    calendars = await local.list_calendars()
    busy = await local.free_busy([c.id for c in calendars], start, end)

    rows = await db.execute(
        text(
            """
            SELECT s.start_at, s.end_at
            FROM core.events_shadow s
            JOIN core.calendar_selections sel
              ON sel.connection_id = s.connection_id
             AND sel.external_calendar_id = s.external_calendar_id
            WHERE s.user_id = :uid
              AND s.deleted_at IS NULL
              AND s.all_day = false
              AND sel.read_enabled = true
              AND s.start_at < :end AND s.end_at > :start
            """
        ),
        {"uid": str(user_id), "start": start, "end": end},
    )
    busy.extend(
        BusyInterval(start=max(r[0], start), end=min(r[1], end))
        for r in rows.all()
    )
    return busy


def _check_window(start: datetime, end: datetime) -> None:
    if end <= start:
        raise ValueError("fim da janela deve ser depois do início")
    if end - start > MAX_WINDOW:
        raise ValueError(
            f"janela de {(end - start).days} dias excede o máximo de "
            f"{MAX_WINDOW.days} dias (§7.0)"
        )


# ---------------------------------------------------------------
# Classificação de flexibilidade (§5.6)
# ---------------------------------------------------------------
# Palavras que indicam compromisso intocável. A lista é deliberadamente
# ampla: o viés é para FIXED, porque mover a consulta médica de alguém
# por heurística errada destrói a confiança de uma vez.
_FIXED_KEYWORDS = (
    "prova",
    "exame",
    "consulta",
    "médic",
    "medic",
    "dentista",
    "voo",
    "viagem",
    "entrevista",
    "reunião",
    "reuniao",
    "apresentação",
    "apresentacao",
    "defesa",
    "aula",
    "casamento",
    "aniversário",
    "aniversario",
    "cirurgia",
    "vacina",
)

_FLEXIBLE_KEYWORDS = (
    "academia",
    "treino",
    "estudo",
    "estudar",
    "revisão",
    "revisao",
    "leitura",
    "ler",
    "exercício",
    "exercicio",
    "caminhada",
    "corrida",
)


def classify_flexibility(
    title: str, *, has_attendees: bool, created_by_age: bool
) -> tuple[Flexibility, str]:
    """Heurística com viés para rígido. Devolve (nível, motivo).

    O motivo é guardado em `flexibility_source` para o usuário entender
    por que o Age classificou daquele jeito — e corrigir se errou.
    """
    if has_attendees:
        # Outra pessoa espera por isso. Nunca move sem permissão.
        return Flexibility.FIXED, "tem convidados"

    lowered = title.lower()

    for keyword in _FIXED_KEYWORDS:
        if keyword in lowered:
            return Flexibility.FIXED, f"título contém '{keyword}'"

    if created_by_age:
        return Flexibility.FLEXIBLE, "criado pelo Age"

    for keyword in _FLEXIBLE_KEYWORDS:
        if keyword in lowered:
            return Flexibility.FLEXIBLE, f"título contém '{keyword}'"

    # Sem sinal: evento pessoal sem convidados pode ser movido dentro do
    # mesmo dia, mas não livremente.
    return Flexibility.SEMI_FLEXIBLE, "evento pessoal, sem sinal claro"
