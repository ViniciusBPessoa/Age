"""Rotas de tarefa.

ARQUITETURA.md §7.1, §5.7.2 e §5.9.5. Duas superfícies, como no
calendário e no estudo: a pública para o front e a interna para o
worker.

A interna existe pela mesma razão das outras: o worker precisa LER a
lista para montar contexto (§5.9.5 só é possível cruzando as duas
fontes), mas não pode escrever — escrever é ato que exige aprovação e
passa pelo `PlanExecutor` (§3.4). Essa assimetria é imposta pelos
GRANTs do Postgres, e aqui ela aparece como ausência de rota de
escrita no router interno.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.calendar import WorkerUser
from app.core.db import user_session
from app.core.deps import CurrentUser, DbSession
from app.ports.tasks import (
    Priority,
    Task,
    TaskError,
    TaskNotFound,
)
from app.services.calendar_access import load_policy
from app.services.plan_store import build_plan_from_items
from app.services.task_store import (
    LOCAL_TASKS,
    counterpart_of_task,
    describe_overview,
    read_tasks,
    summarize_tasks,
)

log = logging.getLogger("core-api.tasks")
router = APIRouter(prefix="/tasks", tags=["tasks"])


# ---------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------
class TaskListOut(BaseModel):
    id: str
    name: str
    is_default: bool
    writable: bool
    color: str | None = None


class ReservedOut(BaseModel):
    event_ref_kind: str
    event_ref_id: str
    title: str
    start: datetime
    end: datetime
    minutes: int


class TaskOut(BaseModel):
    id: str
    ref_kind: str
    list_id: str
    title: str
    notes: str | None
    due_date: date | None
    due_at: datetime | None
    status: str
    completed_at: datetime | None
    priority: str | None
    labels: list[str]
    estimated_min: int | None
    position: int | None
    is_overdue: bool
    # O que faz esta rota diferente de um to-do qualquer (§5.9.5).
    reserved: list[ReservedOut]
    reserved_minutes: int
    provider_name: str | None
    # Campos que o provedor não guarda (§3.3.2). A UI mostra isso no
    # item, não só na tela de conexão: o usuário precisa saber ao olhar
    # a tarefa, não só ao conectar a conta.
    dropped_fields: list[str]


class TaskCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    list_id: str | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    labels: list[str] = Field(default_factory=list)
    estimated_min: int | None = Field(default=None, ge=1, le=1440)


class TaskPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    due_date: date | None = None
    due_at: datetime | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    labels: list[str] | None = None
    estimated_min: int | None = Field(default=None, ge=1, le=1440)
    list_id: str | None = None


class ReorderIn(BaseModel):
    list_id: str
    ordered_ids: list[str] = Field(min_length=1)


class OverviewOut(BaseModel):
    total_open: int
    overdue: list[TaskOut]
    due_soon: list[TaskOut]
    without_due_count: int
    unscheduled_due_soon: list[TaskOut]
    estimated_minutes_unscheduled: int
    summary_text: str


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def _provider(db, user_id: UUID):
    from app.adapters.tasks.local import LocalTasks

    return LocalTasks(db=db, user_id=user_id)


# NENHUMA rota aqui chama `db.commit()`, e isso é deliberado. O commit
# é do `session.begin()` em `core.db.user_session` (§4.2.2): commitar
# no meio do request encerra a transação, e com ela o
# `set_config('app.user_id')`, que tem escopo transacional. O sintoma é
# duplamente confuso — primeiro um `Can't operate on closed
# transaction`, e se a leitura seguinte sobrevivesse, o RLS passaria a
# não ver linha nenhuma. Foi exatamente o que aconteceu ao escrever
# este arquivo.


def _to_out(entry) -> TaskOut:
    task = entry.task
    return TaskOut(
        id=task.id or "",
        ref_kind=entry.ref_kind,
        list_id=task.list_id,
        title=task.title,
        notes=task.notes,
        due_date=task.due_date,
        due_at=task.due_at,
        status=task.status.value,
        completed_at=task.completed_at,
        priority=task.priority.value if task.priority else None,
        labels=list(task.labels),
        estimated_min=task.estimated_min,
        position=task.position,
        is_overdue=task.is_overdue(datetime.now(UTC)),
        reserved=[
            ReservedOut(
                event_ref_kind=b.event_ref_kind,
                event_ref_id=b.event_ref_id,
                title=b.title,
                start=b.start,
                end=b.end,
                minutes=b.minutes,
            )
            for b in entry.reserved
        ],
        reserved_minutes=entry.reserved_minutes,
        provider_name=entry.provider_name,
        dropped_fields=list(task.dropped_fields),
    )


def _user_facing_error(exc: Exception) -> str:
    """Mensagem acionável, sem detalhe técnico (§5.9.13).

    Mesma decisão do M2, tomada depois de um erro cru do SQLAlchemy
    vazar para a resposta da API: detalhe técnico vai para o log, o
    usuário recebe linguagem que permite agir.
    """
    if isinstance(exc, TaskError):
        return exc.message
    log.warning("erro de tarefa não mapeado: %r", exc)
    return "Não consegui concluir essa operação na lista. Tente de novo."


# ---------------------------------------------------------------
# Listas
# ---------------------------------------------------------------
@router.get("/lists", response_model=list[TaskListOut])
async def list_task_lists(user: CurrentUser, db: DbSession) -> list[TaskListOut]:
    provider = _provider(db, user.id)
    # Garante a lista nativa na primeira visita, não no registro: o
    # cadastro não deve falhar por causa de algo secundário.
    await provider.ensure_default_list()
    return [
        TaskListOut(
            id=entry.id,
            name=entry.name,
            is_default=entry.is_default,
            writable=entry.writable,
            color=entry.color,
        )
        for entry in await provider.list_task_lists()
    ]


@router.post("/lists", response_model=TaskListOut, status_code=status.HTTP_201_CREATED)
async def create_task_list(
    payload: Annotated[dict, Field()], user: CurrentUser, db: DbSession
) -> TaskListOut:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Dê um nome à lista.",
        )
    entry = await _provider(db, user.id).create_task_list(
        name, color=payload.get("color")
    )
    return TaskListOut(
        id=entry.id,
        name=entry.name,
        is_default=entry.is_default,
        writable=entry.writable,
        color=entry.color,
    )


# ---------------------------------------------------------------
# Tarefas
# ---------------------------------------------------------------
@router.get("", response_model=list[TaskOut])
async def list_tasks(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[
        Literal["open", "done", "all"], Query(alias="status")
    ] = "open",
    due_before: Annotated[date | None, Query()] = None,
    unscheduled: Annotated[bool, Query()] = False,
) -> list[TaskOut]:
    """Tarefas das duas fontes, com o tempo reservado resolvido.

    `unscheduled=true` atende "mostra o que não tem tempo reservado",
    que é a pergunta de §5.9.5.
    """
    entries = await read_tasks(
        db,
        user.id,
        include_done=status_filter in ("done", "all"),
        due_before=due_before,
        unscheduled_only=unscheduled,
    )
    if status_filter == "done":
        entries = [e for e in entries if e.task.is_done]
    return [_to_out(e) for e in entries]


@router.get("/overview", response_model=OverviewOut)
async def overview(
    user: CurrentUser,
    db: DbSession,
    # Até um ano. O limite de 90 dias da agenda (§7.0) existe porque
    # ler eventos de uma janela longa é caro; aqui a consulta é por
    # `due_date` indexado, e "prazo no fim do semestre" é caso normal
    # de quem estuda — justamente o usuário deste produto.
    horizon_days: Annotated[int, Query(ge=1, le=366)] = 7,
) -> OverviewOut:
    """O panorama de §5.9.5, com os números calculados aqui.

    O modelo não soma nem conta: ele recebe isto pronto. Mesma decisão
    do `Scheduler` — o LLM não inventa tempo livre, e também não
    inventa quantas tarefas vencem.
    """
    data = await summarize_tasks(db, user.id, horizon_days=horizon_days)
    return OverviewOut(
        total_open=data.total_open,
        overdue=[_to_out(e) for e in data.overdue],
        due_soon=[_to_out(e) for e in data.due_soon],
        without_due_count=len(data.without_due),
        unscheduled_due_soon=[_to_out(e) for e in data.unscheduled_due_soon],
        estimated_minutes_unscheduled=data.estimated_minutes_unscheduled,
        summary_text=describe_overview(data),
    )


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateIn, user: CurrentUser, db: DbSession
) -> TaskOut:
    """Criação direta, sem chat (§7.1).

    Existe porque anotar uma tarefa não deveria exigir conversar com um
    modelo. O chat é o caminho rico; este é o caminho rápido.
    """
    provider = _provider(db, user.id)
    list_id = payload.list_id or (await provider.ensure_default_list()).id

    try:
        created = await provider.create_task(
            Task(
                id=None,
                list_id=list_id,
                title=payload.title,
                notes=payload.notes,
                due_date=payload.due_date,
                due_at=payload.due_at,
                priority=(Priority(payload.priority) if payload.priority else None),
                labels=tuple(payload.labels),
                estimated=(
                    timedelta(minutes=payload.estimated_min)
                    if payload.estimated_min
                    else None
                ),
            )
        )
    except TaskError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_user_facing_error(exc),
        ) from exc
    except ValueError as exc:
        # Vem de `Task.__post_init__` — prazo sem fuso, por exemplo.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    entries = await read_tasks(db, user.id, include_done=True)
    entry = next((e for e in entries if e.task.id == created.id), None)
    if entry is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="A tarefa foi criada mas não consegui lê-la de volta.",
        )
    return _to_out(entry)


@router.patch("/{task_id}", response_model=TaskOut)
async def patch_task(
    task_id: str, payload: TaskPatchIn, user: CurrentUser, db: DbSession
) -> TaskOut:
    from dataclasses import replace

    provider = _provider(db, user.id)
    try:
        current = await provider.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essa tarefa não existe.",
        ) from exc

    changes: dict = {}
    if payload.title is not None:
        changes["title"] = payload.title
    if payload.notes is not None:
        changes["notes"] = payload.notes
    if payload.priority is not None:
        changes["priority"] = Priority(payload.priority)
    if payload.labels is not None:
        changes["labels"] = tuple(payload.labels)
    if payload.list_id is not None:
        changes["list_id"] = payload.list_id
    if payload.estimated_min is not None:
        changes["estimated"] = timedelta(minutes=payload.estimated_min)
    # Prazo: mudar `due_at` recalcula `due_date` na entidade, mas o
    # caminho inverso tem de limpar a hora — senão "muda o prazo pra
    # sexta" deixaria a hora da terça pendurada.
    if payload.due_at is not None:
        changes["due_at"] = payload.due_at
        changes["due_date"] = payload.due_at.date()
    elif payload.due_date is not None:
        changes["due_date"] = payload.due_date
        changes["due_at"] = None

    try:
        updated = await provider.update_task(
            replace(current, **changes), expected_etag=current.etag
        )
    except TaskError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_user_facing_error(exc),
        ) from exc

    entries = await read_tasks(db, user.id, include_done=True)
    entry = next(e for e in entries if e.task.id == updated.id)
    return _to_out(entry)


@router.post("/{task_id}/complete", response_model=dict)
async def complete_task(task_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Conclui, e propõe concluir o par ligado (§5.7.1, razão 2).

    Não conclui o outro lado automaticamente: o vínculo é informado, e
    a propagação é explícita. Concluir algo que a pessoa não pediu —
    mesmo o par de algo que ela pediu — é a forma rápida de destruir a
    confiança na lista (§5.7.3).
    """
    provider = _provider(db, user.id)
    try:
        current = await provider.get_task(task_id)
        done = await provider.complete_task(current.list_id, task_id)
    except TaskNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essa tarefa não existe.",
        ) from exc

    linked = await counterpart_of_task(db, user.id, LOCAL_TASKS, task_id)

    message = f"'{done.title}' marcada como feita."
    if linked:
        message += " Ela tem tempo reservado na agenda — quer que eu libere o bloco?"

    return {
        "id": done.id,
        "status": done.status.value,
        "completed_at": (done.completed_at.isoformat() if done.completed_at else None),
        "linked_events": [{"kind": kind, "id": ref} for kind, ref in linked],
        "message": message,
    }


@router.post("/{task_id}/reopen", response_model=dict)
async def reopen_task(task_id: str, user: CurrentUser, db: DbSession) -> dict:
    provider = _provider(db, user.id)
    try:
        current = await provider.get_task(task_id)
        reopened = await provider.reopen_task(current.list_id, task_id)
    except TaskNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essa tarefa não existe.",
        ) from exc
    return {"id": reopened.id, "status": reopened.status.value}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, user: CurrentUser, db: DbSession) -> None:
    provider = _provider(db, user.id)
    try:
        current = await provider.get_task(task_id)
    except TaskNotFound:
        # Idempotente: apagar o que não existe não é erro (§3.3).
        return
    await provider.delete_task(current.list_id, task_id)


@router.post("/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder(payload: ReorderIn, user: CurrentUser, db: DbSession) -> None:
    """Reordena a lista (§5.7.3).

    Só as tarefas citadas mudam de posição; as outras preservam a ordem
    relativa. "Organiza as 5 urgentes" não deve embaralhar as 18
    restantes.
    """
    try:
        await _provider(db, user.id).reorder(payload.list_id, payload.ordered_ids)
    except TaskError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_user_facing_error(exc),
        ) from exc


class ScheduleIn(BaseModel):
    """O bloco a reservar. §8.1: o botão "reservar tempo" da lista."""

    start: datetime
    end: datetime
    title: str | None = Field(default=None, max_length=300)


class ScheduleOut(BaseModel):
    plan_id: UUID
    message: str


@router.post("/{task_id}/schedule", response_model=ScheduleOut)
async def schedule_task(
    task_id: str, body: ScheduleIn, user: CurrentUser, db: DbSession
) -> ScheduleOut:
    """Reserva tempo para uma tarefa, gerando o par ligado (§5.7.1).

    É o botão **reservar tempo** de §8.1: "chama
    `POST /api/tasks/{id}/schedule`, que gera o par ligado — mesmo
    resultado de pedir no chat, sem precisar escrever".

    Duas decisões que esta rota toma, e por quê:

    **Devolve um plano `proposed`, não um fato.** A tentação é escrever
    direto: a pessoa clicou, escolheu o horário, o que falta aprovar?
    Falta o que ela não viu — se o bloco colide com algo, se estoura o
    limite diário (§5.4), se cai numa janela bloqueada. `validate_plan`
    sabe isso e a UI não. Aplicar sem passar pela revisão criaria um
    segundo caminho de escrita sem `PlanDiff`, sem motivo registrado e
    fora do `plan_undo_log` — e aí o "desfazer" do produto deixaria de
    cobrir tudo.

    **O `reason` diz que foi um clique.** §5.6 exige motivo em todo
    item, e o motivo honesto aqui não é um cálculo do Scheduler: é "você
    pediu para reservar". Inventar uma justificativa de otimização que
    não houve seria pior que a frase simples.
    """
    entries = await read_tasks(db, user.id, include_done=False)
    entry = next((e for e in entries if e.task.id == task_id), None)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "task_not_found",
                    "message": "Essa tarefa não existe na sua lista.",
                }
            },
        )

    if body.end <= body.start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "invalid_window",
                    "message": "O fim do bloco tem de vir depois do início.",
                }
            },
        )

    task = entry.task
    minutes = int((body.end - body.start).total_seconds() // 60)
    reason = f"você pediu para reservar {minutes}min para esta tarefa"

    # O motivo do item de tarefa cita um horário, e horário citado para
    # uma pessoa vai no fuso dela (§9.5). Sem isto o texto saía em UTC,
    # e um bloco das 21:15 aparecia como "00:15" do dia seguinte — a
    # pessoa leria que o Age marcou madrugada.
    policy = await load_policy(db, user.id)
    local_start = body.start.astimezone(ZoneInfo(policy.timezone))

    # Os dois itens do par, no formato que `_build_item` consome. O
    # `links` cruzado é o que faz a revisão tratá-los como unidade e o
    # executor revertê-los junto (§5.7.1).
    event_draft_id = "ui-event"
    task_draft_id = "ui-task"
    raw_items = [
        {
            "id": event_draft_id,
            "op": "CREATE",
            "target": "event",
            "reason": reason,
            "links": [task_draft_id],
            "payload": {
                "title": body.title or task.title,
                "start": body.start.isoformat(),
                "end": body.end.isoformat(),
            },
        },
        {
            "id": task_draft_id,
            "op": "UPDATE",
            "target": "task",
            "reason": (
                f"aponta para o bloco reservado em {local_start:%d/%m às %H:%M}"
            ),
            "links": [event_draft_id],
            "payload": {"task_id": task_id, "due_at": body.start.isoformat()},
        },
    ]

    plan_id = await build_plan_from_items(
        db,
        user.id,
        summary=f"Reservar {minutes}min para “{task.title}”",
        raw_items=raw_items,
    )

    return ScheduleOut(
        plan_id=plan_id,
        message="Montei a reserva. Confira e aprove para entrar na agenda.",
    )


@router.get("/{task_id}/links", response_model=list[ReservedOut])
async def task_links(
    task_id: str, user: CurrentUser, db: DbSession
) -> list[ReservedOut]:
    """Os blocos reservados para esta tarefa (§7.1)."""
    entries = await read_tasks(db, user.id, include_done=True)
    entry = next((e for e in entries if e.task.id == task_id), None)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essa tarefa não existe.",
        )
    return [
        ReservedOut(
            event_ref_kind=b.event_ref_kind,
            event_ref_id=b.event_ref_id,
            title=b.title,
            start=b.start,
            end=b.end,
            minutes=b.minutes,
        )
        for b in entry.reserved
    ]


# ---------------------------------------------------------------
# Superfície interna — o worker LÊ, nunca escreve (§3.4)
# ---------------------------------------------------------------
internal = APIRouter(prefix="/internal/tasks", tags=["internal"])


@internal.get("/overview")
async def internal_overview(
    user_id: WorkerUser,
    horizon_days: Annotated[int, Query(ge=1, le=366)] = 7,
) -> dict:
    """Panorama para o prompt (§5.9.5).

    O worker chama isto em vez de consultar o banco direto: a regra de
    "qual tarefa tem tempo reservado" é uma só, e tê-la em dois lugares
    divergiria — a mesma razão pela qual o Planner é chamado por rota
    interna e não reimplementado no worker.
    """
    async with user_session(user_id) as db:
        data = await summarize_tasks(db, user_id, horizon_days=horizon_days)
        return {
            "total_open": data.total_open,
            "overdue_count": len(data.overdue),
            "due_soon_count": len(data.due_soon),
            "without_due_count": len(data.without_due),
            "unscheduled": [
                {
                    "id": e.task.id,
                    "ref_kind": e.ref_kind,
                    "title": e.task.title,
                    "due_date": (
                        e.task.due_date.isoformat() if e.task.due_date else None
                    ),
                    "due_at": (e.task.due_at.isoformat() if e.task.due_at else None),
                    "estimated_min": e.task.estimated_min,
                    "priority": (e.task.priority.value if e.task.priority else None),
                }
                for e in data.unscheduled_due_soon
            ],
            "estimated_minutes_unscheduled": (data.estimated_minutes_unscheduled),
            "summary_text": describe_overview(data),
        }


@internal.get("/list")
async def internal_list(
    user_id: WorkerUser,
    include_done: Annotated[bool, Query()] = False,
    due_before: Annotated[date | None, Query()] = None,
) -> dict:
    async with user_session(user_id) as db:
        entries = await read_tasks(
            db, user_id, include_done=include_done, due_before=due_before
        )
        return {
            "tasks": [
                {
                    "id": e.task.id,
                    "ref_kind": e.ref_kind,
                    "list_id": e.task.list_id,
                    "title": e.task.title,
                    "status": e.task.status.value,
                    "due_date": (
                        e.task.due_date.isoformat() if e.task.due_date else None
                    ),
                    "due_at": (e.task.due_at.isoformat() if e.task.due_at else None),
                    "estimated_min": e.task.estimated_min,
                    "priority": (e.task.priority.value if e.task.priority else None),
                    "labels": list(e.task.labels),
                    "has_reserved_time": e.has_reserved_time,
                    "reserved_minutes": e.reserved_minutes,
                    "dropped_fields": list(e.task.dropped_fields),
                }
                for e in entries
            ]
        }


@internal.get("/lists")
async def internal_lists(user_id: WorkerUser) -> dict:
    """Listas disponíveis e qual é destino de escrita (§5.2)."""
    async with user_session(user_id) as db:
        provider = _provider(db, user_id)
        await provider.ensure_default_list()
        entries = await provider.list_task_lists()
        return {
            "lists": [
                {
                    "id": e.id,
                    "name": e.name,
                    "is_default": e.is_default,
                    "writable": e.writable,
                }
                for e in entries
            ]
        }
