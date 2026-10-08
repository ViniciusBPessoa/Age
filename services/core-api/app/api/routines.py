"""Rotas de rotina, relatório e mudança externa.

ARQUITETURA.md §7.1, §5.9.4, §5.9.9 e §5.10. Duas superfícies, como nas
outras: a pública para o front e a interna para o worker.

A interna tem uma assimetria que vale explicar: o worker **detecta**
mudança externa (o sync roda nele, §3.4) mas não escreve em
`external_change_log` — ele reporta por `POST /internal/routines/
external-change`, e o core-api decide o que conta como conflito. Manter
essa regra num lugar só é o que impede ela de existir em duas versões
que divergem.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.calendar import WorkerUser
from app.core.db import user_session
from app.core.deps import CurrentUser, DbSession
from app.domain.routines import (
    expand,
    find_conflicts,
)
from app.services.calendar_access import load_policy, read_events
from app.services.routine_store import (
    acknowledge_external_changes,
    adherence_for,
    create_routine,
    describe_changes,
    describe_report,
    load_occurrences,
    load_routine,
    load_routines,
    mark_occurrence,
    materialize,
    pending_external_changes,
    period_report,
    record_external_change,
    set_routine_state,
)

log = logging.getLogger("core-api.routines")
router = APIRouter(prefix="/routines", tags=["routines"])


# ---------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------
class RoutineIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    rrule: str = Field(min_length=3, max_length=500)
    duration_min: int = Field(ge=5, le=1440)
    started_at: datetime
    flexibility: Literal["fixed", "semi_flexible", "flexible"] = "flexible"
    target_per_week: int | None = Field(default=None, ge=1, le=21)
    category: str | None = Field(default=None, max_length=50)


class RoutineOut(BaseModel):
    id: str
    title: str
    rrule: str
    duration_min: int
    flexibility: str
    target_per_week: int | None
    category: str | None
    started_at: datetime
    paused_at: datetime | None
    ended_at: datetime | None
    is_active: bool
    next_occurrence: datetime | None


class OccurrenceOut(BaseModel):
    routine_id: str
    routine_title: str
    start: datetime
    end: datetime
    status: str
    actual_min: int | None
    moved_to: datetime | None


class AdherenceOut(BaseModel):
    routine_id: str | None
    routine_title: str
    planned: int
    done: int
    skipped: int
    moved: int
    total_minutes_done: int
    target_per_week: int | None
    per_week: float
    completion: float
    meets_target: bool | None


class MarkOccurrenceIn(BaseModel):
    scheduled_start: datetime
    status: Literal["done", "skipped", "moved"]
    actual_min: int | None = Field(default=None, ge=1, le=1440)
    notes: str | None = Field(default=None, max_length=1000)
    moved_to: datetime | None = None


class ConflictOut(BaseModel):
    start: datetime
    end: datetime
    with_title: str
    blocking: bool


class PreviewOut(BaseModel):
    """O que a série vai gerar, ANTES de ela existir.

    Existe porque §5.9.4 pergunta antes de montar: "19h serve, ou
    prefere manhã?". A resposta útil precisa dos conflitos reais, e
    mostrá-los depois de criar a série seria tarde.
    """

    occurrences: list[OccurrenceOut]
    conflicts: list[ConflictOut]
    blocking_count: int


# ---------------------------------------------------------------
# NENHUMA rota aqui chama `db.commit()`: o commit é do
# `session.begin()` em `core.db.user_session` (§4.2.2). Commitar no
# meio encerra a transação e com ela o `set_config('app.user_id')`,
# que tem escopo transacional — o RLS passaria a não ver linha
# nenhuma.
# ---------------------------------------------------------------
def _routine_out(routine, next_start: datetime | None) -> RoutineOut:
    return RoutineOut(
        id=routine.id or "",
        title=routine.title,
        rrule=routine.rrule,
        duration_min=routine.duration_min,
        flexibility=routine.flexibility.value,
        target_per_week=routine.target_per_week,
        category=routine.category,
        started_at=routine.started_at,
        paused_at=routine.paused_at,
        ended_at=routine.ended_at,
        is_active=routine.is_active,
        next_occurrence=next_start,
    )


def _occurrence_out(occurrence) -> OccurrenceOut:
    return OccurrenceOut(
        routine_id=occurrence.routine_id or "",
        routine_title=occurrence.routine_title,
        start=occurrence.start,
        end=occurrence.end,
        status=occurrence.status.value,
        actual_min=occurrence.actual_min,
        moved_to=occurrence.moved_to,
    )


def _adherence_out(entry) -> AdherenceOut:
    return AdherenceOut(
        routine_id=entry.routine_id,
        routine_title=entry.routine_title,
        planned=entry.planned,
        done=entry.done,
        skipped=entry.skipped,
        moved=entry.moved,
        total_minutes_done=entry.total_minutes_done,
        target_per_week=entry.target_per_week,
        per_week=round(entry.per_week, 2),
        completion=round(entry.completion, 3),
        meets_target=entry.meets_target,
    )


# ---------------------------------------------------------------
# Rotina
# ---------------------------------------------------------------
@router.get("", response_model=list[RoutineOut])
async def list_routines(
    user: CurrentUser,
    db: DbSession,
    include_ended: Annotated[bool, Query()] = False,
) -> list[RoutineOut]:
    from app.domain.routines import next_occurrence

    now = datetime.now(UTC)
    out = []
    for routine in await load_routines(
        db, user.id, include_ended=include_ended
    ):
        nxt = None
        if routine.is_active:
            try:
                found = next_occurrence(routine, after=now)
                nxt = found.start if found else None
            except ValueError:
                # RRULE que entrou no banco antes da validação: a lista
                # não deve quebrar por causa de uma linha ruim.
                log.warning("rrule inválida na rotina %s", routine.id)
        out.append(_routine_out(routine, nxt))
    return out


@router.post("/preview", response_model=PreviewOut)
async def preview(
    payload: RoutineIn, user: CurrentUser, db: DbSession
) -> PreviewOut:
    """Mostra o que a série geraria, sem criar nada.

    É o passo de §5.9.4 em que o assistente diz "seg/qua/sex às 19h é o
    que colide menos — só 2 conflitos no mês". O número vem daqui, não
    do modelo.
    """
    from app.domain.routines import Routine
    from app.ports.calendar import Flexibility

    try:
        probe = Routine(
            id=None,
            title=payload.title,
            rrule=payload.rrule,
            duration=timedelta(minutes=payload.duration_min),
            started_at=payload.started_at,
            flexibility=Flexibility(payload.flexibility),
            target_per_week=payload.target_per_week,
        )
        window_end = payload.started_at + timedelta(days=28)
        occurrences = expand(
            probe,
            window_start=payload.started_at,
            window_end=window_end,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    events = await read_events(db, user.id, payload.started_at, window_end)
    clashes = find_conflicts(occurrences, events)

    return PreviewOut(
        occurrences=[_occurrence_out(o) for o in occurrences],
        conflicts=[
            ConflictOut(
                start=c.occurrence.start,
                end=c.occurrence.end,
                with_title=c.with_title,
                blocking=c.blocking,
            )
            for c in clashes
        ],
        blocking_count=sum(1 for c in clashes if c.blocking),
    )


@router.post("", response_model=RoutineOut, status_code=status.HTTP_201_CREATED)
async def create(
    payload: RoutineIn,
    user: CurrentUser,
    db: DbSession,
    materialize_days: Annotated[int, Query(ge=0, le=365)] = 28,
) -> RoutineOut:
    try:
        routine_id = await create_routine(
            db,
            user.id,
            title=payload.title,
            rrule=payload.rrule,
            duration_min=payload.duration_min,
            started_at=payload.started_at,
            flexibility=payload.flexibility,
            target_per_week=payload.target_per_week,
            category=payload.category,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    routine = await load_routine(db, user.id, routine_id)
    assert routine is not None

    if materialize_days:
        await materialize(
            db,
            user.id,
            routine,
            window_start=payload.started_at,
            window_end=payload.started_at + timedelta(days=materialize_days),
        )

    from app.domain.routines import next_occurrence

    nxt = next_occurrence(routine, after=datetime.now(UTC))
    return _routine_out(routine, nxt.start if nxt else None)


@router.patch("/{routine_id}", response_model=RoutineOut)
async def patch(
    routine_id: UUID,
    user: CurrentUser,
    db: DbSession,
    paused: Annotated[bool | None, Query()] = None,
    ended: Annotated[bool, Query()] = False,
) -> RoutineOut:
    """Pausa, retoma ou encerra — nunca apaga (§5.9.4)."""
    changed = await set_routine_state(
        db, user.id, routine_id, paused=paused, ended=ended
    )
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essa rotina não existe.",
        )

    routine = await load_routine(db, user.id, routine_id)
    assert routine is not None

    from app.domain.routines import next_occurrence

    nxt = None
    if routine.is_active:
        found = next_occurrence(routine, after=datetime.now(UTC))
        nxt = found.start if found else None
    return _routine_out(routine, nxt)


@router.delete("/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_routine(
    routine_id: UUID, user: CurrentUser, db: DbSession
) -> None:
    """DELETE encerra a série, não apaga o histórico.

    Decisão deliberada de §5.9.4: a aderência passada continua valendo.
    Apagar de verdade jogaria fora o dado que dá valor ao painel — e o
    usuário que clica "remover" quer parar a série, não perder o
    registro de que fez academia por três meses.
    """
    if not await set_routine_state(db, user.id, routine_id, ended=True):
        return  # idempotente


@router.get("/{routine_id}/occurrences", response_model=list[OccurrenceOut])
async def occurrences(
    routine_id: UUID,
    user: CurrentUser,
    db: DbSession,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> list[OccurrenceOut]:
    found = await load_occurrences(
        db,
        user.id,
        routine_id=routine_id,
        window_start=from_,
        window_end=to,
    )
    return [_occurrence_out(o) for o in found]


@router.post("/{routine_id}/mark", response_model=dict)
async def mark(
    routine_id: UUID,
    payload: MarkOccurrenceIn,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    """Registra o que aconteceu — categoria "registro de fato" (§5.2).

    Executa de verdade e não passa por diff: "pulei a academia ontem" é
    fato sobre o passado, não proposta. Tratar como plano forçaria o
    usuário a "aprovar" que faltou.
    """
    if payload.status == "moved" and payload.moved_to is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Para marcar como movida, diga para quando — senão não "
                "há como distinguir de pulada."
            ),
        )

    ok = await mark_occurrence(
        db,
        user.id,
        routine_id=routine_id,
        scheduled_start=payload.scheduled_start,
        status=payload.status,
        actual_min=payload.actual_min,
        notes=payload.notes,
        moved_to=payload.moved_to,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Não encontrei essa ocorrência. Talvez ela ainda não "
                "tenha sido materializada."
            ),
        )

    routine = await load_routine(db, user.id, routine_id)
    message = f"Marquei como {payload.status}."
    if routine is not None:
        now = datetime.now(UTC)
        # Mesma janela do endpoint de aderência, e pelo mesmo motivo:
        # logo depois de marcar a primeira sessão de uma série nova, o
        # usuário receberia "0 de 0" se a janela parasse em amanhã.
        entry = await adherence_for(
            db,
            user.id,
            routine,
            period_start=now - timedelta(days=28),
            period_end=now + timedelta(days=28),
        )
        return {
            "status": payload.status,
            "message": message,
            "adherence": _adherence_out(entry).model_dump(),
        }
    return {"status": payload.status, "message": message}


@router.get("/{routine_id}/adherence", response_model=AdherenceOut)
async def adherence(
    routine_id: UUID,
    user: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=7, le=366)] = 28,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> AdherenceOut:
    """Aderência da série num período.

    Sem `from`/`to`, a janela cobre os últimos `days` dias **e o
    futuro já materializado**. A primeira versão ia de `now - days` até
    `now + 1 day`, e com isso uma rotina que começa na semana que vem
    reportava zero de zero — a pessoa criava a série, via "0 feitas" e
    concluía que não funcionou.

    Incluir o futuro também é o que faz `planned` significar "o que
    está marcado", que é o denominador honesto: comparar o feito com
    "o que já deveria ter acontecido" esconde o tamanho do
    compromisso.
    """
    routine = await load_routine(db, user.id, routine_id)
    if routine is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essa rotina não existe.",
        )
    now = datetime.now(UTC)
    period_start = from_ or (now - timedelta(days=days))
    period_end = to or (now + timedelta(days=days))

    entry = await adherence_for(
        db,
        user.id,
        routine,
        period_start=period_start,
        period_end=period_end,
    )
    return _adherence_out(entry)


@router.post("/{routine_id}/materialize", response_model=dict)
async def materialize_window(
    routine_id: UUID,
    user: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 28,
) -> dict:
    routine = await load_routine(db, user.id, routine_id)
    if routine is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essa rotina não existe.",
        )
    now = datetime.now(UTC)
    created = await materialize(
        db,
        user.id,
        routine,
        window_start=now,
        window_end=now + timedelta(days=days),
    )
    return {"created": created}


# ---------------------------------------------------------------
# Relatório (§5.9.9) e mudança externa (§5.10)
# ---------------------------------------------------------------
reports = APIRouter(prefix="/reports", tags=["reports"])


class ReportOut(BaseModel):
    period_start: datetime
    period_end: datetime
    study_planned: int
    study_done: int
    study_skipped: int
    study_planned_minutes: int
    study_done_minutes: int
    study_completion: float
    routines: list[AdherenceOut]
    events_total: int
    patterns: list[str]
    summary_text: str


@reports.get("/period", response_model=ReportOut)
async def report_period(
    user: CurrentUser,
    db: DbSession,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> ReportOut:
    """Planejado vs. realizado (§5.9.9).

    Sem o período, usa os últimos 7 dias — "como foi minha semana?" é a
    pergunta que origina esta rota.
    """
    now = datetime.now(UTC)
    period_end = end or now
    period_start = start or (period_end - timedelta(days=7))

    if period_start >= period_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O início do período tem de vir antes do fim.",
        )

    policy = await load_policy(db, user.id)
    data = await period_report(
        db,
        user.id,
        period_start=period_start,
        period_end=period_end,
        timezone_name=policy.timezone,
    )
    return ReportOut(
        period_start=data.period_start,
        period_end=data.period_end,
        study_planned=data.study.planned,
        study_done=data.study.done,
        study_skipped=data.study.skipped,
        study_planned_minutes=data.study.planned_minutes,
        study_done_minutes=data.study.done_minutes,
        study_completion=round(data.study.completion, 3),
        routines=[_adherence_out(a) for a in data.routines],
        events_total=data.events_total,
        patterns=list(data.patterns),
        summary_text=describe_report(data),
    )


changes = APIRouter(prefix="/calendar", tags=["calendar"])


class ExternalChangeOut(BaseModel):
    id: UUID
    change_kind: str
    title: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    conflicts_with_title: str | None
    detected_at: datetime
    is_conflict: bool


@changes.get("/external-changes", response_model=dict)
async def external_changes(
    user: CurrentUser,
    db: DbSession,
    acknowledge: Annotated[bool, Query()] = False,
) -> dict:
    """Mudanças feitas fora do Age, ainda não reconhecidas (§5.10).

    Isto **não** é notificação: o assistente não inicia conversa
    (§1.2). O front chama ao abrir o chat, e a primeira resposta começa
    reconhecendo — reativo, não proativo.

    `acknowledge=true` marca como vistas. Sem isso, a mesma reunião
    seria anunciada em toda mensagem.
    """
    pending = await pending_external_changes(db, user.id)
    policy = await load_policy(db, user.id)
    payload = {
        "changes": [
            ExternalChangeOut(
                id=c.id,
                change_kind=c.change_kind,
                title=c.title,
                starts_at=c.starts_at,
                ends_at=c.ends_at,
                conflicts_with_title=c.conflicts_with_title,
                detected_at=c.detected_at,
                is_conflict=c.is_conflict,
            ).model_dump()
            for c in pending
        ],
        "conflict_count": sum(1 for c in pending if c.is_conflict),
        "summary_text": describe_changes(
            pending, timezone_name=policy.timezone
        ),
    }

    if acknowledge and pending:
        await acknowledge_external_changes(
            db, user.id, [c.id for c in pending]
        )

    return payload


# ---------------------------------------------------------------
# Superfície interna
# ---------------------------------------------------------------
internal = APIRouter(prefix="/internal/routines", tags=["internal"])


@internal.get("/list")
async def internal_list(user_id: WorkerUser) -> dict:
    async with user_session(user_id) as db:
        from app.domain.routines import next_occurrence

        now = datetime.now(UTC)
        out = []
        for routine in await load_routines(db, user_id):
            try:
                nxt = next_occurrence(routine, after=now)
            except ValueError:
                nxt = None
            out.append(
                {
                    "id": routine.id,
                    "title": routine.title,
                    "rrule": routine.rrule,
                    "duration_min": routine.duration_min,
                    "flexibility": routine.flexibility.value,
                    "target_per_week": routine.target_per_week,
                    "is_active": routine.is_active,
                    "paused": routine.paused_at is not None,
                    "next_occurrence": nxt.start.isoformat() if nxt else None,
                }
            )
        return {"routines": out}


@internal.get("/adherence")
async def internal_adherence(
    user_id: WorkerUser,
    days: Annotated[int, Query(ge=7, le=366)] = 28,
) -> dict:
    async with user_session(user_id) as db:
        now = datetime.now(UTC)
        out = []
        for routine in await load_routines(db, user_id):
            entry = await adherence_for(
                db,
                user_id,
                routine,
                period_start=now - timedelta(days=days),
                period_end=now + timedelta(days=1),
            )
            if entry.planned:
                out.append(_adherence_out(entry).model_dump())
        return {"adherence": out}


@internal.get("/report")
async def internal_report(
    user_id: WorkerUser,
    days: Annotated[int, Query(ge=1, le=366)] = 7,
) -> dict:
    """O relatório que o assistente cita (§5.9.9).

    Os números e o padrão vêm calculados. O modelo redige, e o system
    prompt proíbe elogio que o dado não sustenta.
    """
    async with user_session(user_id) as db:
        now = datetime.now(UTC)
        policy = await load_policy(db, user_id)
        data = await period_report(
            db,
            user_id,
            period_start=now - timedelta(days=days),
            period_end=now,
            timezone_name=policy.timezone,
        )
        return {
            "study": {
                "planned": data.study.planned,
                "done": data.study.done,
                "skipped": data.study.skipped,
                "planned_minutes": data.study.planned_minutes,
                "done_minutes": data.study.done_minutes,
                "completion": round(data.study.completion, 3),
            },
            "routines": [_adherence_out(a).model_dump() for a in data.routines],
            "events_total": data.events_total,
            "patterns": list(data.patterns),
            "summary_text": describe_report(data),
        }


@internal.get("/external-changes")
async def internal_external_changes(user_id: WorkerUser) -> dict:
    """O que mudou fora do Age e ainda não foi reconhecido (§5.10).

    O worker lê para montar a abertura da conversa. Reconhecer é da
    rota pública, chamada pelo front: marcar como visto aqui faria a
    mudança ser "reconhecida" sem o usuário ter lido nada.
    """
    async with user_session(user_id) as db:
        pending = await pending_external_changes(db, user_id)
        policy = await load_policy(db, user_id)
        return {
            "count": len(pending),
            "conflict_count": sum(1 for c in pending if c.is_conflict),
            "summary_text": describe_changes(
                pending, timezone_name=policy.timezone
            ),
        }


@internal.post("/{routine_id}/mark")
async def internal_mark(
    routine_id: UUID, payload: MarkOccurrenceIn, user_id: WorkerUser
) -> dict:
    """Registro de fato pelo chat (§5.2).

    Esta é a única rota de ESCRITA na superfície interna de todo o
    projeto, e a exceção é justificada: registrar que algo aconteceu no
    passado não é mudança de agenda, não precisa de aprovação, e tratar
    como proposta forçaria o usuário a "aprovar" que faltou à academia.

    Quem escreve continua sendo o core-api — o worker chama a rota, não
    o banco. O GRANT dele em `core` segue sendo só SELECT.
    """
    if payload.status == "moved" and payload.moved_to is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Para marcar como movida, diga para quando — senão não "
                "há como distinguir de pulada."
            ),
        )

    async with user_session(user_id) as db:
        ok = await mark_occurrence(
            db,
            user_id,
            routine_id=routine_id,
            scheduled_start=payload.scheduled_start,
            status=payload.status,
            actual_min=payload.actual_min,
            notes=payload.notes,
            moved_to=payload.moved_to,
        )
        if not ok:
            return {
                "error": (
                    "não encontrei essa ocorrência; talvez ela ainda não "
                    "tenha sido materializada"
                )
            }

        routine = await load_routine(db, user_id, routine_id)
        if routine is None:
            return {"status": payload.status}

        now = datetime.now(UTC)
        entry = await adherence_for(
            db,
            user_id,
            routine,
            period_start=now - timedelta(days=28),
            period_end=now + timedelta(days=28),
        )
        return {
            "status": payload.status,
            "routine_title": routine.title,
            "adherence": _adherence_out(entry).model_dump(),
        }


class ExternalChangeIn(BaseModel):
    change_kind: Literal["created", "moved", "deleted", "updated"]
    title: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    event_ref_kind: str | None = None
    event_ref_id: str | None = None


@internal.post("/external-change")
async def internal_record_change(
    payload: ExternalChangeIn, user_id: WorkerUser
) -> dict:
    """O worker reporta; o core-api decide o que conta como conflito.

    O sync roda no worker (§3.4), mas a regra de "isso colide com algo
    que o Age marcou" fica aqui. Tê-la nos dois lugares é como ela
    passaria a divergir — e o worker nem tem GRANT de escrita em
    `core`.
    """
    async with user_session(user_id) as db:
        change_id = await record_external_change(
            db,
            user_id,
            change_kind=payload.change_kind,
            title=payload.title,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            event_ref_kind=payload.event_ref_kind,
            event_ref_id=payload.event_ref_id,
        )
        return {"id": str(change_id) if change_id else None}
