"""Rotas de plano: revisar, aprovar, rejeitar, desfazer.

ARQUITETURA.md §5.7, §5.8 e §8.1. Esta é a superfície da aprovação
granular: aceitar 4 de 6 itens é operação de primeira classe, não caso
de borda.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.adapters.calendar.local import LocalCalendar
from app.adapters.tasks.local import LocalTasks
from app.core.deps import CurrentUser, DbSession
from app.domain.plan_executor import (
    PlanExpired,
    RevalidationFailed,
    apply_plan,
)
from app.ports.tasks import Task
from app.services.plan_store import (
    load_plan,
    mark_applied,
    mark_rejected,
    promote_drafts,
    record_undo_log,
)
from app.services.study_store import link_sessions_to_events

log = logging.getLogger("core-api.plans")
router = APIRouter(prefix="/plans", tags=["plans"])


class ConflictOut(BaseModel):
    kind: str
    message: str
    severity: str


class PlanItemOut(BaseModel):
    id: UUID
    target: str
    op: str
    reason: str
    before: dict | None = None
    after: dict | None = None
    conflicts: list[ConflictOut] = Field(default_factory=list)
    links: list[UUID] = Field(default_factory=list)
    status: str
    is_blocking: bool


class PlanOut(BaseModel):
    id: UUID
    conversation_id: UUID | None
    origin_note: str | None
    status: str
    summary: str
    items: list[PlanItemOut]
    created_at: datetime
    expires_at: datetime
    is_expired: bool


class ApplyIn(BaseModel):
    """`item_ids=None` aplica tudo; lista vazia é rejeição."""

    item_ids: list[UUID] | None = None
    # Confirmação separada para itens com conflito bloqueante (§5.6).
    confirm_blocking: bool = False


class ApplyOut(BaseModel):
    status: str
    applied_count: int
    failed: list[dict] = Field(default_factory=list)
    manual_fix_needed: list[str] = Field(default_factory=list)
    message: str


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": {"code": "plan_not_found", "message": "Plano não encontrado."}
        },
    )


@router.get("")
async def list_plans(
    user: CurrentUser,
    db: DbSession,
    status_filter: Literal["proposed", "applied", "all"] = Query(
        default="proposed", alias="status"
    ),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[PlanOut]:
    # Promove rascunhos do worker antes de listar (§2.3.2).
    await promote_drafts(db, user.id)

    clause = ""
    if status_filter == "proposed":
        clause = "AND status = 'proposed'"
    elif status_filter == "applied":
        clause = "AND status IN ('applied','partially_applied')"

    rows = await db.execute(
        text(
            f"""
            SELECT id FROM core.plans
            WHERE user_id = :uid {clause}
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"uid": str(user.id), "lim": limit},
    )

    out = []
    for row in rows.all():
        plan = await load_plan(db, user.id, row[0])
        if plan is not None:
            out.append(_to_out(plan))
    return out


@router.get("/{plan_id}")
async def get_plan(
    plan_id: UUID, user: CurrentUser, db: DbSession
) -> PlanOut:
    await promote_drafts(db, user.id)
    plan = await load_plan(db, user.id, plan_id)
    if plan is None:
        raise _not_found()
    return _to_out(plan)


@router.post("/{plan_id}/apply")
async def apply(
    plan_id: UUID, body: ApplyIn, user: CurrentUser, db: DbSession
) -> ApplyOut:
    """Aplica o plano. É o único caminho que escreve na agenda."""
    plan = await load_plan(db, user.id, plan_id)
    if plan is None:
        raise _not_found()

    if body.item_ids is not None and not body.item_ids:
        await mark_rejected(db, user.id, plan_id)
        return ApplyOut(
            status="rejected",
            applied_count=0,
            message="Plano rejeitado. Nada foi alterado na sua agenda.",
        )

    provider = LocalCalendar(db=db, user_id=user.id)
    # A porta de tarefa vai sempre, mesmo em plano só de agenda: o
    # executor só a usa se houver item `Target.TASK`, e instanciar é
    # barato. A alternativa — decidir aqui se o plano tem tarefa —
    # duplicaria no router uma regra que é do executor.
    tasks = LocalTasks(db=db, user_id=user.id)

    try:
        result = await apply_plan(
            plan,
            provider,
            tasks=tasks,
            item_ids=body.item_ids,
            skip_revalidation=False,
        )
    except PlanExpired as exc:
        await db.execute(
            text(
                "UPDATE core.plans SET status = 'expired' "
                "WHERE id = :pid AND user_id = :uid"
            ),
            {"pid": str(plan_id), "uid": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "plan_expired",
                    "message": (
                        "Este plano expirou e sua agenda pode ter mudado. "
                        "Quer que eu recalcule?"
                    ),
                    "details": {"plan_id": str(plan_id)},
                }
            },
        ) from exc
    except RevalidationFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "agenda_changed",
                    "message": (
                        "Sua agenda mudou desde que eu propus isso: "
                        + "; ".join(exc.conflicts)
                        + ". Quer que eu recalcule?"
                    ),
                    "details": {"conflicts": exc.conflicts},
                }
            },
        ) from exc

    await mark_applied(db, user.id, plan_id, result)
    await record_undo_log(db, user.id, plan_id, result)

    # Liga as sessões de estudo aos eventos recém-criados (§5.5). Até
    # aqui as sessões existem em `study_sessions` sem `event_ref`;
    # agora passam a apontar para o evento real, o que habilita o
    # acompanhamento de §5.9.8 ("pulei o estudo de ontem").
    #
    # Falha aqui não derruba a aplicação: os eventos já estão na agenda,
    # e perder o vínculo é degradação (o painel de estudo fica
    # incompleto), não corrupção.
    try:
        linked = await link_sessions_to_events(db, user.id, plan_id)
        if linked:
            log.info("%d sessões de estudo vinculadas", linked)
    except Exception as exc:  # noqa: BLE001
        log.warning("não foi possível vincular sessões de estudo: %s", exc)

    # Persiste o par tarefa↔evento (§5.7.1). Mesma política de
    # degradação: o par já está aplicado nas duas portas, e perder o
    # vínculo significa o Age não saber dizer "3h reservadas quinta" —
    # incômodo, não corrupção.
    try:
        pairs = await _persist_task_event_links(db, user.id, plan, result)
        if pairs:
            log.info("%d pares tarefa/evento vinculados", pairs)
    except Exception as exc:  # noqa: BLE001
        log.warning("não foi possível vincular tarefa e evento: %s", exc)

    # Erro tecnico fica no log; o usuario recebe linguagem util
    # (secao 5.9.13). Vazar stack de driver de banco na UI nao ajuda
    # ninguem e expoe estrutura interna.
    for item_id, error in result.failed:
        log.warning("item %s falhou ao aplicar: %s", item_id, error)

    return ApplyOut(
        status=result.status.value,
        applied_count=len(result.applied),
        failed=[
            {"item_id": str(i), "error": _user_facing_error(e)}
            for i, e in result.failed
        ],
        manual_fix_needed=result.manual_fix_needed,
        message=_message_for(result),
    )


async def _persist_task_event_links(db, user_id, plan, result) -> int:
    """Grava em `task_event_links` os pares que acabaram de ser aplicados.

    O vínculo só é gravado quando **os dois lados** foram aplicados com
    sucesso. Gravar com meio par aplicado seria pior que não gravar: o
    Age afirmaria ter reservado tempo para uma tarefa que não existe,
    ou apontaria para um bloco que não foi criado.

    Como o executor reverte o grupo inteiro na falha parcial (§5.8),
    "os dois aplicados" é a situação normal; esta checagem cobre o caso
    em que a reversão também falhou.
    """
    from app.domain.plan_diff import Target
    from app.services.task_store import (
        LOCAL_EVENTS,
        LOCAL_TASKS,
        create_link,
    )

    applied_ref = {r.item_id: r.applied_ref for r in result.applied}
    pairs = 0

    for item in plan.items:
        if item.target != Target.TASK or not item.links:
            continue
        task_ref = applied_ref.get(item.id)
        if not task_ref:
            continue

        for linked_id in item.links:
            other = plan.item(linked_id)
            if other is None or other.target != Target.EVENT:
                continue
            event_ref = applied_ref.get(other.id)
            if not event_ref:
                # O evento não foi aplicado: não há bloco para apontar.
                continue

            await create_link(
                db,
                user_id,
                task_ref_kind=LOCAL_TASKS,
                task_ref_id=str(task_ref),
                event_ref_kind=LOCAL_EVENTS,
                event_ref_id=str(event_ref),
                plan_id=plan.id,
            )
            pairs += 1

    return pairs


@router.post("/{plan_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject(plan_id: UUID, user: CurrentUser, db: DbSession) -> None:
    plan = await load_plan(db, user.id, plan_id)
    if plan is None:
        raise _not_found()
    await mark_rejected(db, user.id, plan_id)


class ItemRescheduleIn(BaseModel):
    """Novo horário para um item ainda não aplicado."""

    start: datetime
    end: datetime


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_item(
    item_id: UUID, user: CurrentUser, db: DbSession
) -> None:
    """Descarta UM item proposto, sem mexer nos outros do mesmo plano.

    Existe por um relato direto: "está lá como proposta, não posso mais
    alterar, não posso mais editar". O `reject` do plano inteiro já
    existia, mas quem recebeu 18 blocos de estudo e quer tirar 3 não
    tinha como — era tudo ou nada.

    Só toca item `pending`: aplicado já virou evento na agenda, e
    apagar de lá é operação de agenda, não de plano (§5.8).
    """
    row = await db.execute(
        text(
            """
            UPDATE core.plan_items SET status = 'rejected'
            WHERE id = :iid AND user_id = :uid AND status = 'pending'
            RETURNING plan_id
            """
        ),
        {"iid": str(item_id), "uid": str(user.id)},
    )
    plan_id = row.scalar_one_or_none()
    if plan_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "item_not_found",
                    "message": "Item não encontrado ou já decidido.",
                }
            },
        )

    # Plano sem nenhum item pendente não deve continuar `proposed`:
    # ficaria para sempre na lista de pendências e no contador da
    # agenda, pedindo uma aprovação que não tem mais o que aprovar.
    restantes = await db.execute(
        text(
            "SELECT count(*) FROM core.plan_items "
            "WHERE plan_id = :pid AND user_id = :uid AND status = 'pending'"
        ),
        {"pid": str(plan_id), "uid": str(user.id)},
    )
    if restantes.scalar_one() == 0:
        await db.execute(
            text(
                "UPDATE core.plans SET status = 'rejected' "
                "WHERE id = :pid AND user_id = :uid AND status = 'proposed'"
            ),
            {"pid": str(plan_id), "uid": str(user.id)},
        )


@router.patch("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def reschedule_item(
    item_id: UUID, body: ItemRescheduleIn, user: CurrentUser, db: DbSession
) -> None:
    """Muda o horário de um item proposto antes de aprová-lo.

    O par natural do descarte: "esse bloco serve, mas não às 8h15".
    Sem isto a única saída é rejeitar e pedir tudo de novo ao modelo,
    gastando um turno para mover meia hora.

    Edita só `start` e `end` do payload. Título e calendário saem do
    que foi proposto: o que esta rota resolve é horário, e abrir o
    payload inteiro à edição transformaria aprovação em formulário.
    """
    if body.end <= body.start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "invalid_window",
                    "message": "O fim precisa vir depois do início.",
                }
            },
        )

    row = await db.execute(
        text(
            """
            UPDATE core.plan_items
            -- `CAST(... AS text)` e não `:start::text`: o `::` do cast
            -- colide com o marcador de parâmetro do SQLAlchemy, e o
            -- Postgres recebe a query com um `:` solto.
            SET after = jsonb_set(
                    jsonb_set(
                        CAST(after AS jsonb), '{start}',
                        to_jsonb(CAST(:start AS text))
                    ),
                    '{end}', to_jsonb(CAST(:end AS text))
                )
            WHERE id = :iid AND user_id = :uid
              AND status = 'pending' AND target = 'event'
              AND after IS NOT NULL
            RETURNING id
            """
        ),
        {
            "iid": str(item_id),
            "uid": str(user.id),
            "start": body.start.isoformat(),
            "end": body.end.isoformat(),
        },
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "item_not_found",
                    "message": "Item não encontrado ou já decidido.",
                }
            },
        )


@router.post("/{plan_id}/undo")
async def undo(plan_id: UUID, user: CurrentUser, db: DbSession) -> dict:
    """Desfaz um plano aplicado, dentro da janela de 7 dias (§5.8).

    A reversão é compensatória: deletar o que criamos, mover de volta o
    que movemos, recriar o que deletamos. Fingir atomicidade seria
    mentira, então o que não deu é reportado.
    """
    rows = await db.execute(
        text(
            """
            SELECT item_id, op, snapshot, applied_ref, target
            FROM core.plan_undo_log
            WHERE plan_id = :pid AND user_id = :uid AND undone_at IS NULL
            ORDER BY created_at DESC
            """
        ),
        {"pid": str(plan_id), "uid": str(user.id)},
    )
    records = rows.all()
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "nothing_to_undo",
                    "message": "Não há nada para desfazer neste plano.",
                }
            },
        )

    provider = LocalCalendar(db=db, user_id=user.id)
    tasks = LocalTasks(db=db, user_id=user.id)
    undone = 0
    problems: list[str] = []

    for _item_id, op, snapshot, applied_ref, target in records:
        # SAVEPOINT por item. Sem isto, a primeira exceção aborta a
        # transação inteira e todos os itens seguintes falham com
        # `current transaction is aborted` — o try/except por item não
        # funciona no Postgres sem savepoint.
        #
        # Importa porque a reversão é compensatória (§5.8): desfazer 3
        # de 4 é melhor que desfazer nenhum, e o usuário precisa saber
        # exatamente o que sobrou.
        try:
            async with db.begin_nested():
                if target == "task":
                    await _undo_task(tasks, op, snapshot, applied_ref)
                elif op == "CREATE" and applied_ref:
                    await provider.delete_event("", applied_ref)
                elif op in ("MOVE", "UPDATE") and snapshot:
                    await provider.restore_from_snapshot(snapshot)
                elif op == "DELETE" and applied_ref:
                    await provider.restore_event(applied_ref)
            undone += 1
        except Exception as exc:  # noqa: BLE001
            title = (snapshot or {}).get("title", applied_ref or "item")
            log.warning("falha ao desfazer %s de '%s': %s", op, title, exc)
            problems.append(f"'{title}' não pôde ser revertido")

    # Os vínculos criados pelo plano saem junto: um par desfeito não
    # deve continuar afirmando que a tarefa tem tempo reservado.
    try:
        from app.services.task_store import remove_links_for_plan

        await remove_links_for_plan(db, user.id, plan_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("não foi possível remover vínculos do plano: %s", exc)

    await db.execute(
        text(
            """
            UPDATE core.plan_undo_log SET undone_at = now()
            WHERE plan_id = :pid AND user_id = :uid AND undone_at IS NULL
            """
        ),
        {"pid": str(plan_id), "uid": str(user.id)},
    )

    message = f"Desfiz {undone} de {len(records)} mudanças."
    if problems:
        message += " Não consegui desfazer: " + "; ".join(problems)

    return {
        "undone_count": undone,
        "total": len(records),
        "problems": problems,
        "message": message,
    }


async def _undo_task(tasks, op: str, snapshot: dict | None, ref: str | None):
    """Reversão compensatória na porta de tarefa (§5.8).

    `COMPLETE` é o caso que merece atenção: desfazer significa REABRIR,
    e uma tarefa marcada como feita por engano desaparece da lista sem
    a pessoa saber. É a reversão que mais importa funcionar.
    """
    if op == "CREATE" and ref:
        await tasks.delete_task("", ref)
    elif op == "DELETE" and ref:
        await tasks.restore_task(ref)
    elif op == "COMPLETE" and ref:
        await tasks.reopen_task((snapshot or {}).get("list_id", ""), ref)
    elif op in ("MOVE", "UPDATE") and snapshot:
        from app.services.plan_store import _task_from_json

        await tasks.update_task(_task_from_json(snapshot))


# ---------------------------------------------------------------
def _to_out(plan) -> PlanOut:
    """Serializa o plano para a revisão granular de §8.1.

    O `side_dict` trata evento **e** tarefa, e isso não é zelo: desde o
    M4 o `PlanDiff` é heterogêneo (§5.7), e `before`/`after` são
    `Event | Task | None`.

    A primeira versão desta função assumia evento sempre, e o resultado
    era um `AttributeError: 'Task' object has no attribute 'start'` —
    **500 em `GET /plans/{id}` para qualquer plano com item de tarefa**.
    Nenhum teste pegou porque a suíte do M4 monta e aplica planos pelo
    `plan_store` e pelo executor, sem passar por esta rota; o smoke do
    M4 lê o plano pelo banco. Quem encontrou foi a tela de revisão, que
    é o primeiro código a realmente buscar o diff pela API — o par
    tarefa+evento de §5.7.1 era justamente o caso que não abria.

    O `_kind` é o mesmo discriminador que `plan_store` grava no JSON, e
    está aqui por consistência com ele: a UI já sabe lê-lo.
    """

    def side_dict(side) -> dict | None:
        if side is None:
            return None

        # Tarefa: prazo, não intervalo. §5.7 insiste na diferença —
        # "ligar pro dentista sexta às 15h" tem hora e NÃO bloqueia a
        # agenda, então mandar `start`/`end` faria a UI desenhar uma
        # ocupação que não existe.
        if isinstance(side, Task):
            return {
                "_kind": "task",
                "id": side.id,
                "list_id": side.list_id,
                "title": side.title,
                "notes": side.notes,
                "due_date": side.due_date.isoformat() if side.due_date else None,
                "due_at": side.due_at.isoformat() if side.due_at else None,
                "status": side.status.value,
                "priority": side.priority.value if side.priority else None,
                "estimated_min": (
                    int(side.estimated.total_seconds() // 60)
                    if side.estimated
                    else None
                ),
            }

        return {
            "_kind": "event",
            "id": side.id,
            "title": side.title,
            "start": side.start.isoformat(),
            "end": side.end.isoformat(),
            "flexibility": side.flexibility.value,
        }

    return PlanOut(
        id=plan.id,
        conversation_id=plan.conversation_id,
        origin_note=plan.origin_note,
        status=plan.status.value,
        summary=plan.summary,
        created_at=plan.created_at,
        expires_at=plan.expires_at,
        is_expired=plan.is_expired,
        items=[
            PlanItemOut(
                id=i.id,
                target=i.target.value,
                op=i.op.value,
                reason=i.reason,
                before=side_dict(i.before),
                after=side_dict(i.after),
                conflicts=[
                    ConflictOut(
                        kind=c.kind.value, message=c.message, severity=c.severity
                    )
                    for c in i.conflicts
                ],
                links=list(i.links),
                status=i.status.value,
                is_blocking=i.is_blocking,
            )
            for i in plan.items
        ],
    )


def _user_facing_error(raw: str) -> str:
    """Traduz falha tecnica em algo acionavel.

    O detalhe tecnico vai para o log, nao para a tela.
    """
    lowered = raw.lower()
    if "conflito bloqueante" in lowered:
        return raw  # ja e mensagem nossa, em portugues
    if "etag" in lowered or "concurrency" in lowered:
        return "Esse evento foi alterado por fora. Recalcule o plano."
    if "not_found" in lowered or "nao existe" in lowered:
        return "Esse evento nao esta mais na sua agenda."
    if "permission denied" in lowered:
        return "Sem permissao para escrever nesse calendario."
    if "invalid uuid" in lowered or "asyncpg" in lowered or "sql" in lowered:
        return "Nao consegui gravar esse item. Tente novamente."
    return "Nao consegui aplicar esse item."


def _message_for(result) -> str:
    """Mensagem no padrão de §5.9.13: o que entrou, o que não, e o próximo passo."""
    from app.domain.plan_diff import PlanStatus

    applied = len(result.applied)
    failed = len(result.failed)

    if result.status == PlanStatus.APPLIED:
        return f"Pronto. {applied} mudança(s) na sua agenda."

    if result.status == PlanStatus.PARTIALLY_APPLIED:
        base = (
            f"{applied} entraram na agenda, {failed} falharam."
        )
        if result.manual_fix_needed:
            base += " Precisa de atenção manual: " + "; ".join(
                result.manual_fix_needed
            )
        else:
            base += " Nada ficou pela metade."
        return base

    reasons = "; ".join(_user_facing_error(e) for _, e in result.failed)
    return f"Nenhuma mudança foi aplicada. {reasons}" 
