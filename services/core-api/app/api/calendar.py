"""Rotas de agenda.

ARQUITETURA.md §7. Duas superfícies:

- **pública** (`/api/v1/calendar/*`) — o front, autenticado por JWT de
  usuário.
- **interna** (`/internal/calendar/*`) — o ai-worker, autenticado por
  JWT de serviço. Fora do ingress (§7.3).

A interna existe porque o worker lê agenda mas não é dono dela (§3.4).
Duplicar o Scheduler lá seria pior: regra delicada em duas versões
diverge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.adapters.calendar.local import LocalCalendar
from app.core.config import get_settings
from app.core.db import user_session
from app.core.deps import CurrentUser, DbSession
from app.domain.scheduler import find_free_slots, summarize_workload
from app.ports.calendar import Event, Flexibility
from app.services.calendar_access import (
    classify_flexibility,
    load_policy,
    read_busy,
    read_events,
)

log = logging.getLogger("core-api.calendar")
router = APIRouter(prefix="/calendar", tags=["calendar"])


# ---------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------
class EventOut(BaseModel):
    id: str
    calendar_id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    description: str | None = None
    location: str | None = None
    recurrence: str | None = None
    flexibility: str
    created_by_age: bool
    etag: str | None = None


class EventList(BaseModel):
    events: list[EventOut]
    count: int


class CalendarOut(BaseModel):
    id: str
    name: str
    timezone: str
    is_primary: bool
    writable: bool
    color: str | None = None


class CreateEventIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    start: datetime
    end: datetime
    calendar_id: str | None = None
    description: str | None = Field(default=None, max_length=5000)
    location: str | None = Field(default=None, max_length=300)
    all_day: bool = False
    recurrence: str | None = Field(default=None, max_length=500)
    flexibility: Literal["fixed", "semi_flexible", "flexible"] | None = None


class UpdateFlexibilityIn(BaseModel):
    flexibility: Literal["fixed", "semi_flexible", "flexible"]


class SlotOut(BaseModel):
    start: datetime
    end: datetime
    score: float
    reasons: list[str]


class SlotList(BaseModel):
    slots: list[SlotOut]
    count: int
    hint: str | None = None


# ---------------------------------------------------------------
def _to_out(event: Event) -> EventOut:
    return EventOut(
        id=event.id or "",
        calendar_id=event.calendar_id,
        title=event.title,
        start=event.start,
        end=event.end,
        all_day=event.all_day,
        description=event.description,
        location=event.location,
        recurrence=event.recurrence,
        flexibility=event.flexibility.value,
        created_by_age=event.source == "age",
        etag=event.etag,
    )


def _bad_window(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": {"code": "invalid_window", "message": message}},
    )


# ---------------------------------------------------------------
# Público
# ---------------------------------------------------------------
@router.get("/calendars")
async def list_calendars(
    user: CurrentUser, db: DbSession
) -> list[CalendarOut]:
    """Calendários do usuário.

    Cria o nativo no primeiro acesso: `LocalCalendar` é o default de
    conta nova (§3.2.1), e criar aqui em vez de no registro evita que o
    cadastro falhe por algo secundário.
    """
    local = LocalCalendar(db=db, user_id=user.id)
    refs = await local.list_calendars()
    return [
        CalendarOut(
            id=r.id,
            name=r.name,
            timezone=r.timezone,
            is_primary=r.is_primary,
            writable=r.writable,
            color=r.color,
        )
        for r in refs
    ]


@router.get("/events")
async def list_events(
    user: CurrentUser,
    db: DbSession,
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> EventList:
    try:
        events = await read_events(db, user.id, start, end)
    except ValueError as exc:
        raise _bad_window(str(exc)) from exc

    return EventList(events=[_to_out(e) for e in events], count=len(events))


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def create_event(
    body: CreateEventIn, user: CurrentUser, db: DbSession
) -> EventOut:
    """Criação direta, sem passar pelo chat.

    Existe porque abrir o chat para marcar dentista quinta 15h é atrito
    desnecessário — e porque a timeline precisa de edição manual.
    """
    local = LocalCalendar(db=db, user_id=user.id)

    calendar_id = body.calendar_id
    if calendar_id is None:
        default = await local.ensure_default_calendar()
        calendar_id = default.id

    if body.flexibility is not None:
        flexibility = Flexibility(body.flexibility)
    else:
        # Classificação automática, com viés para rígido (§5.6).
        flexibility, _ = classify_flexibility(
            body.title, has_attendees=False, created_by_age=False
        )

    try:
        created = await local.create_event(
            Event(
                id=None,
                calendar_id=calendar_id,
                title=body.title,
                start=body.start,
                end=body.end,
                description=body.description,
                location=body.location,
                all_day=body.all_day,
                recurrence=body.recurrence,
                flexibility=flexibility,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "invalid_event", "message": str(exc)}},
        ) from exc

    return _to_out(created)


@router.patch("/events/{event_id}/flexibility")
async def set_flexibility(
    event_id: str,
    body: UpdateFlexibilityIn,
    user: CurrentUser,
    db: DbSession,
) -> EventOut:
    """Fixar ou soltar um evento.

    O usuário sempre pode corrigir a heurística: é o cadeado da
    timeline (§8.1).
    """
    from sqlalchemy import text

    row = (
        await db.execute(
            text(
                """
                UPDATE core.local_events
                SET flexibility = :flex,
                    flexibility_source = 'definido pelo usuário',
                    version = version + 1, updated_at = now()
                WHERE id = :eid AND user_id = :uid AND deleted_at IS NULL
                RETURNING id, local_calendar_id, title, start_at, end_at,
                          all_day, description, location, recurrence,
                          flexibility, created_by_age, version
                """
            ),
            {"eid": event_id, "uid": str(user.id), "flex": body.flexibility},
        )
    ).first()

    if row is None:
        # Evento externo: a flexibilidade vive na sombra (§6.3).
        shadow = (
            await db.execute(
                text(
                    """
                    UPDATE core.events_shadow
                    SET flexibility = :flex,
                        flexibility_source = 'definido pelo usuário'
                    WHERE external_event_id = :eid AND user_id = :uid
                    RETURNING external_event_id, external_calendar_id, title,
                              start_at, end_at, all_day, NULL, location,
                              recurrence, flexibility, created_by_age, etag
                    """
                ),
                {"eid": event_id, "uid": str(user.id), "flex": body.flexibility},
            )
        ).first()
        if shadow is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "event_not_found",
                        "message": "Evento não encontrado.",
                    }
                },
            )
        row = shadow

    return EventOut(
        id=str(row[0]),
        calendar_id=str(row[1]),
        title=row[2],
        start=row[3],
        end=row[4],
        all_day=row[5],
        description=row[6],
        location=row[7],
        recurrence=row[8],
        flexibility=row[9],
        created_by_age=row[10],
        etag=str(row[11]),
    )


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: str, user: CurrentUser, db: DbSession
) -> None:
    local = LocalCalendar(db=db, user_id=user.id)
    await local.delete_event("", event_id)


@router.get("/free-slots")
async def get_free_slots(
    user: CurrentUser,
    db: DbSession,
    start: datetime = Query(...),
    end: datetime = Query(...),
    duration_min: int = Query(..., ge=5, le=600),
    deadline: datetime | None = Query(default=None),
    count: int = Query(default=10, ge=1, le=50),
) -> SlotList:
    """Horários livres, calculados deterministicamente (§5.3).

    Exposto no público porque a UI também precisa: o botão "reservar
    tempo" não passa pelo chat.
    """
    try:
        busy = await read_busy(db, user.id, start, end)
    except ValueError as exc:
        raise _bad_window(str(exc)) from exc

    policy = await load_policy(db, user.id)
    slots = find_free_slots(
        busy=busy,
        window_start=start,
        window_end=end,
        duration=timedelta(minutes=duration_min),
        policy=policy,
        deadline=deadline,
        max_results=count,
    )

    hint = None
    if not slots:
        hint = (
            "Nenhum horário livre nessa janela. Tente uma janela maior, "
            "sessões mais curtas, ou mover algo flexível."
        )

    return SlotList(
        slots=[
            SlotOut(
                start=s.start,
                end=s.end,
                score=round(s.score, 3),
                reasons=list(s.reasons),
            )
            for s in slots
        ],
        count=len(slots),
        hint=hint,
    )


@router.get("/workload")
async def get_workload(
    user: CurrentUser,
    db: DbSession,
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> dict:
    """Carga por dia — responde "minha semana está pesada?" (§5.9.1)."""
    try:
        busy = await read_busy(db, user.id, start, end)
        events = await read_events(db, user.id, start, end)
    except ValueError as exc:
        raise _bad_window(str(exc)) from exc

    policy = await load_policy(db, user.id)
    per_day: dict = {}
    for event in events:
        day = event.start.astimezone(policy.tz).date()
        per_day[day] = per_day.get(day, 0) + 1

    loads = summarize_workload(busy, per_day, start, end, policy)
    return {
        "days": [
            {
                "day": load.day.isoformat(),
                "busy_hours": round(load.busy.total_seconds() / 3600, 2),
                "event_count": load.event_count,
                "over_limit": load.over_limit,
            }
            for load in loads
        ],
        "max_hours_per_day": policy.max_hours_per_day.total_seconds() / 3600,
    }


# ---------------------------------------------------------------
# Interno: o worker lê a agenda por aqui (§3.4)
# ---------------------------------------------------------------
internal = APIRouter(prefix="/internal/calendar", tags=["internal"])


async def require_worker_token(
    authorization: str = Header(default=""),
    x_age_user_id: str = Header(default=""),
) -> UUID:
    """Valida o JWT de serviço e extrai o usuário do header.

    O `user_id` vem em header separado, não no token: o token prova que
    é o worker, o header diz de quem é o trabalho. Quem garante que o
    worker só pede o usuário certo é o payload da fila, que o core-api
    produziu depois de validar o JWT do usuário (§4.1.1).
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "Token ausente."}},
        )

    try:
        payload = jwt.decode(
            authorization.removeprefix("Bearer ").strip(),
            get_settings().service_jwt_secret,
            algorithms=["HS256"],
            audience="core-api",
            options={"require": ["exp", "aud"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "Token inválido."}},
        ) from exc

    if payload.get("typ") != "service":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "Tipo inesperado."}},
        )

    try:
        return UUID(x_age_user_id)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "missing_user",
                    "message": "Header X-Age-User-Id obrigatório.",
                }
            },
        ) from exc


WorkerUser = Annotated[UUID, Depends(require_worker_token)]


@internal.get("/events")
async def internal_events(
    user_id: WorkerUser,
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> dict:
    # Sessão própria com `app.user_id` do usuário dono do job: a leitura
    # cruzada também passa por RLS (§4.2.3).
    async with user_session(user_id) as db:
        try:
            events = await read_events(db, user_id, start, end)
        except ValueError as exc:
            raise _bad_window(str(exc)) from exc

        return {
            "events": [
                {
                    "id": e.id,
                    "title": e.title,
                    "start": e.start.isoformat(),
                    "end": e.end.isoformat(),
                    "flexibility": e.flexibility.value,
                }
                for e in events
            ]
        }


@internal.get("/free-slots")
async def internal_free_slots(
    user_id: WorkerUser,
    start: datetime = Query(...),
    end: datetime = Query(...),
    duration_min: int = Query(..., ge=5, le=600),
    deadline: datetime | None = Query(default=None),
    count: int = Query(default=10, ge=1, le=50),
) -> dict:
    async with user_session(user_id) as db:
        try:
            busy = await read_busy(db, user_id, start, end)
        except ValueError as exc:
            raise _bad_window(str(exc)) from exc

        policy = await load_policy(db, user_id)
        slots = find_free_slots(
            busy=busy,
            window_start=start,
            window_end=end,
            duration=timedelta(minutes=duration_min),
            policy=policy,
            deadline=deadline,
            max_results=count,
        )
        return {
            "slots": [
                {
                    "start": s.start.isoformat(),
                    "end": s.end.isoformat(),
                    "score": s.score,
                    "reasons": list(s.reasons),
                }
                for s in slots
            ]
        }


@internal.get("/workload")
async def internal_workload(
    user_id: WorkerUser,
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> dict:
    async with user_session(user_id) as db:
        try:
            busy = await read_busy(db, user_id, start, end)
            events = await read_events(db, user_id, start, end)
        except ValueError as exc:
            raise _bad_window(str(exc)) from exc

        policy = await load_policy(db, user_id)
        per_day: dict = {}
        for event in events:
            day = event.start.astimezone(policy.tz).date()
            per_day[day] = per_day.get(day, 0) + 1

        loads = summarize_workload(busy, per_day, start, end, policy)
        return {
            "days": [
                {
                    "day": load.day.isoformat(),
                    "busy_hours": round(load.busy.total_seconds() / 3600, 2),
                    "event_count": load.event_count,
                    "over_limit": load.over_limit,
                }
                for load in loads
            ]
        }
