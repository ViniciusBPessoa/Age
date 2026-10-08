"""Rotas de estudo.

ARQUITETURA.md §5.5, §5.9.8 e §8.1. Duas superfícies, como no
calendário: a pública para o front e a interna para o worker.

A interna existe porque o Planner é regra de negócio delicada
(pré-requisitos, espaçamento, DST) e tê-la em duas versões — uma no
core-api, outra no worker — divergiria.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.calendar import WorkerUser
from app.core.db import user_session
from app.core.deps import CurrentUser, DbSession
from app.domain.planner import Topic, build_study_plan, describe_shortfall
from app.services.calendar_access import load_policy, read_busy
from app.services.study_store import (
    learned_preferences,
    link_sessions_to_events,
    list_subjects,
    mark_session,
    pending_sessions,
    persist_study_plan,
    save_learned_preference,
    subject_progress,
)

log = logging.getLogger("core-api.study")
router = APIRouter(prefix="/study", tags=["study"])


# ---------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------
class TopicIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    estimated_min: int = Field(ge=5, le=600)
    prerequisites: list[str] = Field(default_factory=list)


class SubjectOut(BaseModel):
    id: str
    name: str
    deadline: str | None
    status: str
    total_estimated_min: int | None
    sessions_planned: int
    sessions_done: int


class ProgressOut(BaseModel):
    subject_id: UUID
    name: str
    deadline: datetime | None
    total_estimated_min: int
    total_done_min: int
    sessions_planned: int
    sessions_done: int
    sessions_skipped: int
    topics_total: int
    topics_completed: int
    completion: float


class MarkSessionIn(BaseModel):
    status: Literal["done", "skipped"]
    actual_min: int | None = Field(default=None, ge=1, le=600)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=1000)


class PreferenceIn(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    value: object
    confirmed: bool = False


# ---------------------------------------------------------------
# Núcleo compartilhado entre público e interno
# ---------------------------------------------------------------
async def _build_plan(
    db,
    user_id: UUID,
    *,
    subject: str,
    topics: list[dict],
    deadline: str | None,
    start_from: str | None,
    include_reviews: bool,
) -> dict:
    """Monta o plano de estudo. Usado pelas duas superfícies."""
    policy = await load_policy(db, user_id)
    tz = policy.tz

    def parse(value: str | None) -> datetime | None:
        if not value:
            return None
        moment = datetime.fromisoformat(value)
        return moment if moment.tzinfo else moment.replace(tzinfo=tz)

    deadline_at = parse(deadline)
    window_start = parse(start_from) or datetime.now(UTC)

    # Janela: até o prazo, ou 30 dias se não houver. Trinta porque é o
    # horizonte em que um plano de estudo ainda faz sentido — além
    # disso a agenda muda demais.
    if deadline_at is not None:
        window_end = deadline_at
    else:
        window_end = window_start + timedelta(days=30)

    if window_end <= window_start:
        return {
            "error": "o prazo já passou",
            "fits": False,
            "sessions": [],
        }

    domain_topics = [
        Topic(
            name=t["name"],
            estimated=timedelta(minutes=int(t["estimated_min"])),
            order=index,
            prerequisites=tuple(t.get("prerequisites") or ()),
        )
        for index, t in enumerate(topics)
    ]

    busy = await read_busy(db, user_id, window_start, window_end)

    # Preferências aprendidas sobrepõem a política base: "nada antes
    # das 10h" dito numa conversa vale aqui (§5.9.7).
    learned = await learned_preferences(db, user_id)
    policy = _apply_learned(policy, learned)

    plan = build_study_plan(
        subject=subject,
        topics=domain_topics,
        busy=busy,
        window_start=window_start,
        window_end=window_end,
        policy=policy,
        deadline=deadline_at,
        include_reviews=include_reviews,
    )

    days = {s.start.astimezone(tz).date() for s in plan.sessions}

    return {
        "subject": plan.subject,
        "fits": plan.fits,
        "sessions": [
            {
                "title": s.title(plan.subject),
                "topic": s.topic_name,
                "kind": s.kind.value,
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "duration_min": int(s.duration.total_seconds() // 60),
                "reason": _session_reason(s, plan),
            }
            for s in plan.sessions
        ],
        "total_hours": round(plan.total_scheduled.total_seconds() / 3600, 1),
        "estimated_hours": round(
            plan.total_estimated.total_seconds() / 3600, 1
        ),
        "days_used": len(days),
        "slack_days": plan.slack_days,
        "unplaced": [
            {"topic": name, "minutes": int(d.total_seconds() // 60)}
            for name, d in plan.unplaced
        ],
        "shortfall": describe_shortfall(plan),
    }


def _session_reason(session, plan) -> str:
    """Justificativa que vai no cartão de aprovação (§5.7).

    Gerada aqui, a partir do cálculo real — não pelo modelo. Assim a
    explicação corresponde ao que de fato decidiu o horário.
    """
    parts = list(session.reasons)

    if session.kind.value == "review":
        return parts[0] if parts else "revisão espaçada para fixar"

    if session.of_parts > 1:
        parts.insert(0, f"parte {session.part} de {session.of_parts}")

    if not parts:
        return "horário livre antes do prazo"
    return ", ".join(parts)


def _apply_learned(policy, learned: dict):
    """Aplica preferências aprendidas sobre a política base."""
    from dataclasses import replace
    from datetime import time

    from app.domain.scheduler import DailyWindow

    if not learned:
        return policy

    updated = policy

    # "nada antes das 10h"
    floor = learned.get("no_study_before")
    if isinstance(floor, str):
        try:
            limit = time.fromisoformat(floor)
            new_hours = {}
            for weekday, windows in policy.working_hours.items():
                adjusted = []
                for w in windows:
                    start = max(w.start, limit)
                    if start < w.end:
                        adjusted.append(DailyWindow(start=start, end=w.end))
                if adjusted:
                    new_hours[weekday] = tuple(adjusted)
            updated = replace(updated, working_hours=new_hours)
        except ValueError:
            log.info("preferência 'no_study_before' inválida: %r", floor)

    # "nunca no fim de semana"
    if learned.get("no_weekend_study") is True:
        updated = replace(
            updated,
            working_hours={
                d: w for d, w in updated.working_hours.items() if d < 5
            },
        )

    # "sessões de no máximo N minutos"
    cap = learned.get("max_session_min")
    if isinstance(cap, int) and 15 <= cap <= 300:
        updated = replace(updated, session_max=timedelta(minutes=cap))

    return updated


# ---------------------------------------------------------------
# Público
# ---------------------------------------------------------------
class PlanPreviewIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    topics: list[TopicIn]
    deadline: datetime | None = None
    start_from: datetime | None = None
    include_reviews: bool = True


@router.post("/plan/preview")
async def preview_plan(
    body: PlanPreviewIn, user: CurrentUser, db: DbSession
) -> dict:
    """Simula um plano sem gravar nada.

    Existe para o painel de estudo poder mostrar "se você estudar isso,
    fica assim" antes de o usuário se comprometer.
    """
    return await _build_plan(
        db,
        user.id,
        subject=body.subject,
        topics=[t.model_dump() for t in body.topics],
        deadline=body.deadline.isoformat() if body.deadline else None,
        start_from=body.start_from.isoformat() if body.start_from else None,
        include_reviews=body.include_reviews,
    )


class CreateSubjectIn(BaseModel):
    """Materializa um plano de estudo nas tabelas de acompanhamento."""

    subject: str = Field(min_length=1, max_length=200)
    topics: list[TopicIn]
    deadline: datetime | None = None
    start_from: datetime | None = None
    include_reviews: bool = True
    syllabus: str | None = Field(default=None, max_length=20000)
    # Plano cujos eventos já foram aprovados, para ligar as sessões.
    plan_id: UUID | None = None


@router.post("/subjects", status_code=status.HTTP_201_CREATED)
async def create_subject(
    body: CreateSubjectIn, user: CurrentUser, db: DbSession
) -> dict:
    """Grava assunto, tópicos e sessões.

    Separado do `preview` de propósito: prever é barato e não deixa
    rastro; materializar cria compromisso de acompanhamento. Misturar
    os dois faria toda simulação sujar o painel de estudo.
    """
    built = await _build_plan(
        db,
        user.id,
        subject=body.subject,
        topics=[t.model_dump() for t in body.topics],
        deadline=body.deadline.isoformat() if body.deadline else None,
        start_from=body.start_from.isoformat() if body.start_from else None,
        include_reviews=body.include_reviews,
    )

    if built.get("error"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "cannot_plan",
                    "message": built["error"],
                }
            },
        )

    # Reconstrói o StudyPlan de domínio a partir do resultado, para
    # reusar a mesma persistência que o chat usa.
    from datetime import datetime as _dt

    from app.domain.planner import (
        PlannedSession,
        SessionKind,
        StudyPlan,
    )
    from app.domain.planner import (
        Topic as DomainTopic,
    )

    domain_topics = [
        DomainTopic(
            name=t.name,
            estimated=timedelta(minutes=t.estimated_min),
            order=index,
            prerequisites=tuple(t.prerequisites),
        )
        for index, t in enumerate(body.topics)
    ]

    plan = StudyPlan(
        subject=built["subject"],
        sessions=[
            PlannedSession(
                topic_name=s["topic"],
                kind=SessionKind(s["kind"]),
                start=_dt.fromisoformat(s["start"]),
                end=_dt.fromisoformat(s["end"]),
            )
            for s in built["sessions"]
        ],
        unplaced=[
            (u["topic"], timedelta(minutes=u["minutes"]))
            for u in built["unplaced"]
        ],
        total_estimated=timedelta(
            minutes=sum(t.estimated_min for t in body.topics)
        ),
        deadline=body.deadline,
    )

    subject_id = await persist_study_plan(
        db,
        user.id,
        plan,
        domain_topics,
        plan_id=body.plan_id,
        syllabus=body.syllabus,
    )

    if body.plan_id is not None:
        await link_sessions_to_events(db, user.id, body.plan_id)

    return {
        "subject_id": str(subject_id),
        "sessions_created": len(plan.sessions),
        "fits": built["fits"],
        "shortfall": built["shortfall"],
    }


@router.get("/subjects")
async def get_subjects(user: CurrentUser, db: DbSession) -> list[SubjectOut]:
    return [SubjectOut(**s) for s in await list_subjects(db, user.id)]


@router.get("/subjects/{subject_id}")
async def get_subject(
    subject_id: UUID, user: CurrentUser, db: DbSession
) -> ProgressOut:
    progress = await subject_progress(db, user.id, subject_id)
    if progress is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "subject_not_found",
                    "message": "Assunto não encontrado.",
                }
            },
        )

    return ProgressOut(
        subject_id=progress.subject_id,
        name=progress.name,
        deadline=progress.deadline,
        total_estimated_min=int(progress.total_estimated.total_seconds() // 60),
        total_done_min=int(progress.total_done.total_seconds() // 60),
        sessions_planned=progress.sessions_planned,
        sessions_done=progress.sessions_done,
        sessions_skipped=progress.sessions_skipped,
        topics_total=progress.topics_total,
        topics_completed=progress.topics_completed,
        completion=round(progress.completion, 3),
    )


@router.get("/sessions/pending")
async def get_pending(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict]:
    """Sessões que já passaram e não foram marcadas.

    Alimenta a marcação rápida de §8.1 — abrir o chat para dizer "fui
    à academia" é atrito demais.
    """
    return await pending_sessions(db, user.id, limit=limit)


@router.post("/sessions/{session_id}/mark")
async def mark(
    session_id: UUID, body: MarkSessionIn, user: CurrentUser, db: DbSession
) -> dict:
    """Registra feito ou pulado.

    Não passa por aprovação: é fato sobre o passado, não proposta
    (§5.2). O que vem depois — reagendar — é que entra no diff.
    """
    try:
        return await mark_session(
            db,
            user.id,
            session_id,
            status=body.status,
            actual_min=body.actual_min,
            difficulty=body.difficulty,
            notes=body.notes,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "session_not_found",
                    "message": "Sessão não encontrada.",
                }
            },
        ) from exc


@router.get("/preferences/learned")
async def get_learned(user: CurrentUser, db: DbSession) -> dict:
    return await learned_preferences(db, user.id, only_confirmed=False)


@router.put("/preferences/learned")
async def put_learned(
    body: PreferenceIn, user: CurrentUser, db: DbSession
) -> dict:
    await save_learned_preference(
        db,
        user.id,
        body.key,
        body.value,
        source="manual",
        confirmed=body.confirmed,
    )
    return {"saved": True, "key": body.key}


# ---------------------------------------------------------------
# Interno: o worker chama por aqui
# ---------------------------------------------------------------
internal = APIRouter(prefix="/internal/study", tags=["internal"])


class InternalPlanIn(BaseModel):
    subject: str
    topics: list[dict]
    deadline: str | None = None
    start_from: str | None = None
    include_reviews: bool = True


@internal.post("/plan")
async def internal_plan(body: InternalPlanIn, user_id: WorkerUser) -> dict:
    async with user_session(user_id) as db:
        return await _build_plan(
            db,
            user_id,
            subject=body.subject,
            topics=body.topics,
            deadline=body.deadline,
            start_from=body.start_from,
            include_reviews=body.include_reviews,
        )


@internal.get("/progress")
async def internal_progress(
    user_id: WorkerUser, subject: str | None = Query(default=None)
) -> dict:
    async with user_session(user_id) as db:
        subjects = await list_subjects(db, user_id)

        if subject:
            lowered = subject.lower()
            subjects = [
                s for s in subjects if lowered in s["name"].lower()
            ]

        enriched = []
        for item in subjects:
            progress = await subject_progress(
                db, user_id, UUID(item["id"])
            )
            if progress is None:
                continue
            enriched.append(
                {
                    **item,
                    "sessions_skipped": progress.sessions_skipped,
                    "topics_completed": progress.topics_completed,
                    "topics_total": progress.topics_total,
                    "done_min": int(
                        progress.total_done.total_seconds() // 60
                    ),
                }
            )

        pending = await pending_sessions(db, user_id, limit=10)
        return {"subjects": enriched, "pending_sessions": pending}


class InternalMarkIn(BaseModel):
    session_id: str
    status: Literal["done", "skipped"]
    actual_min: int | None = None
    difficulty: int | None = None


@internal.post("/mark")
async def internal_mark(body: InternalMarkIn, user_id: WorkerUser) -> dict:
    async with user_session(user_id) as db:
        try:
            return await mark_session(
                db,
                user_id,
                UUID(body.session_id),
                status=body.status,
                actual_min=body.actual_min,
                difficulty=body.difficulty,
            )
        except (LookupError, ValueError) as exc:
            return {"error": str(exc)}


class InternalPrefIn(BaseModel):
    key: str
    value: object
    confirmed: bool = False


@internal.post("/preference")
async def internal_preference(
    body: InternalPrefIn, user_id: WorkerUser
) -> dict:
    async with user_session(user_id) as db:
        await save_learned_preference(
            db,
            user_id,
            body.key,
            body.value,
            source="chat",
            confirmed=body.confirmed,
        )
        return {"saved": True}
