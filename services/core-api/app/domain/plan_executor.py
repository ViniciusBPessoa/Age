"""Aplicação de plano aprovado, com reversão compensatória.

ARQUITETURA.md §5.8. A parte mais delicada do produto, por um motivo
estrutural: **não há transação distribuída com a API do Google.**

A reversão é compensatória, não transacional: deletar o que criamos,
mover de volta o que movemos, recriar o que deletamos. Cada passo é
registrado, e se a reversão também falhar o usuário recebe a lista
literal do que precisa arrumar à mão. Fingir atomicidade aqui seria
mentira.

Recebe o provedor por parâmetro (porta, §3.2) e não sabe se é Google,
local ou CalDAV.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from uuid import UUID

from app.domain.plan_diff import (
    DiffItem,
    ItemStatus,
    Op,
    Plan,
    PlanStatus,
    Target,
    ordered_for_apply,
)
from app.ports.calendar import (
    CalendarProvider,
    Event,
)
from app.ports.tasks import Task, TaskProvider

log = logging.getLogger("core-api.plan_executor")


@dataclass
class AppliedRecord:
    """O que foi feito, para poder desfazer (§5.8)."""

    item_id: UUID
    op: Op
    applied_ref: str | None
    snapshot_before: Event | Task | None
    # Qual porta recebeu. A reversão precisa saber: desfazer um CREATE
    # de tarefa é `delete_task`, não `delete_event`, e sem isto o undo
    # chamaria a porta errada.
    target: Target = Target.EVENT
    # Ordem anterior, para desfazer um REORDER.
    previous_order: tuple[str, ...] = ()


@dataclass
class ExecutionResult:
    status: PlanStatus
    applied: list[AppliedRecord] = field(default_factory=list)
    failed: list[tuple[UUID, str]] = field(default_factory=list)
    # Itens que a reversão também não conseguiu desfazer. É a lista que
    # o usuário recebe para arrumar à mão.
    manual_fix_needed: list[str] = field(default_factory=list)
    revalidation_conflicts: list[str] = field(default_factory=list)
    # Recusado ANTES de escrever qualquer coisa. Não é desfecho do
    # plano: nada foi tentado, e a proposta continua esperando decisão.
    # Quem chama usa isto para NÃO marcar o plano como encerrado, que
    # era o que tornava a recusa permanente (nem o F5 trazia de volta).
    refused_upfront: bool = False

    @property
    def ok(self) -> bool:
        return self.status == PlanStatus.APPLIED


class PlanExpired(Exception):
    """Plano velho demais para aplicar com segurança (§5.7)."""


class RevalidationFailed(Exception):
    """A agenda mudou desde a proposta.

    Abortar é melhor que aplicar sobre um estado diferente do que o
    usuário viu ao aprovar.
    """

    def __init__(self, conflicts: list[str]) -> None:
        super().__init__("a agenda mudou desde a proposta")
        self.conflicts = conflicts


async def apply_plan(
    plan: Plan,
    provider: CalendarProvider,
    *,
    tasks: TaskProvider | None = None,
    item_ids: list[UUID] | None = None,
    skip_revalidation: bool = False,
    confirm_blocking: bool = False,
) -> ExecutionResult:
    """Aplica os itens aceitos, na ordem segura.

    `item_ids` permite aprovação granular: aceitar 4 de 6 é válido
    (§8.1). Itens ligados são tratados como grupo — aceitar meio par
    deixaria estado inconsistente (§5.7.1).
    """
    if plan.is_expired:
        raise PlanExpired(
            f"plano criado em {plan.created_at:%d/%m %H:%M} expirou em "
            f"{plan.expires_at:%d/%m %H:%M}"
        )

    selected = _select_items(plan, item_ids)
    if not selected:
        return ExecutionResult(status=PlanStatus.REJECTED)

    # Bloqueio explícito: item com conflito `blocking` não entra sem
    # confirmação separada. COM a confirmação, entra.
    #
    # `confirm_blocking` existia como campo na rota desde sempre e era
    # jogado fora: o parâmetro não chegava aqui, e esta função recusava
    # incondicionalmente. O efeito era a pior resposta possível, porque
    # ela parecia sucesso: HTTP 200, `applied_count: 0`, nenhum erro na
    # tela, e o cartão sumia. "Tu nunca confirma porra nenhuma" é o
    # relato, e ele estava descrevendo exatamente isto.
    #
    # Todos os motivos são liberáveis, por decisão de produto: a agenda
    # é da pessoa, o app avisa qual é o choque e obedece. Quem resolve
    # o caso de "horário no passado" não é uma recusa dura aqui, é a
    # janela de tolerância em `plan_diff` (§5.7), que impede o conflito
    # de nascer por causa do atraso da própria promoção.
    blocked = [i for i in selected if i.is_blocking]
    if blocked and not confirm_blocking:
        return ExecutionResult(
            status=PlanStatus.FAILED,
            refused_upfront=True,
            failed=[
                (
                    i.id,
                    "conflito bloqueante: "
                    + "; ".join(c.message for c in i.conflicts),
                )
                for i in blocked
            ],
        )

    # Um plano com item de tarefa exige a porta de tarefa. Falhar aqui,
    # antes de aplicar qualquer coisa, é melhor que falhar no meio e ter
    # de reverter — a aplicação parcial é o estado mais caro de todos.
    if any(i.target == Target.TASK for i in selected) and tasks is None:
        return ExecutionResult(
            status=PlanStatus.FAILED,
            refused_upfront=True,
            failed=[
                (i.id, "plano tem item de tarefa, mas nenhuma lista "
                       "está conectada para recebê-lo")
                for i in selected
                if i.target == Target.TASK
            ],
        )

    if not skip_revalidation:
        conflicts = await _revalidate(selected, provider, tasks)
        if conflicts:
            raise RevalidationFailed(conflicts)

    result = ExecutionResult(status=PlanStatus.APPLIED)
    groups = _group_linked(plan, selected)

    for group in groups:
        group_applied: list[AppliedRecord] = []
        group_failed = False

        for item in ordered_for_apply(group):
            try:
                record = await _apply_item(item, provider, tasks)
                group_applied.append(record)
            except Exception as exc:  # noqa: BLE001
                log.warning("item %s falhou: %s", item.id, exc)
                result.failed.append((item.id, str(exc)))
                group_failed = True
                break

        if group_failed and len(group) > 1:
            # Par ligado pela metade é estado inconsistente: bloco na
            # agenda sem tarefa, ou tarefa apontando para horário vazio
            # (§5.7.1). Reverte o grupo inteiro.
            log.info("revertendo grupo de %d itens", len(group_applied))
            unresolved = await _revert(group_applied, provider, tasks)
            result.manual_fix_needed.extend(unresolved)
        else:
            result.applied.extend(group_applied)

    if result.failed and result.applied:
        result.status = PlanStatus.PARTIALLY_APPLIED
    elif result.failed:
        result.status = PlanStatus.FAILED

    return result


# ---------------------------------------------------------------
async def _apply_item(
    item: DiffItem,
    provider: CalendarProvider,
    tasks: TaskProvider | None = None,
) -> AppliedRecord:
    if item.target == Target.TASK:
        if tasks is None:
            raise RuntimeError("item de tarefa sem porta de tarefa")
        return await _apply_task_item(item, tasks)

    match item.op:
        case Op.CREATE:
            assert item.after is not None
            created = await provider.create_event(
                replace(item.after, source="age")
            )
            return AppliedRecord(
                item_id=item.id,
                op=Op.CREATE,
                applied_ref=created.id,
                snapshot_before=None,
            )

        case Op.MOVE | Op.UPDATE:
            assert item.after is not None and item.before is not None
            updated = await provider.update_event(
                replace(item.after, id=item.before.id),
                expected_etag=item.before.etag,
            )
            return AppliedRecord(
                item_id=item.id,
                op=item.op,
                applied_ref=updated.id,
                snapshot_before=item.before,
            )

        case Op.DELETE:
            assert item.before is not None
            await provider.delete_event(
                item.before.calendar_id,
                item.before.id or "",
                expected_etag=item.before.etag,
            )
            return AppliedRecord(
                item_id=item.id,
                op=Op.DELETE,
                applied_ref=item.before.id,
                snapshot_before=item.before,
            )

        case _:
            raise NotImplementedError(f"operação {item.op} não suportada")


async def _apply_task_item(
    item: DiffItem, tasks: TaskProvider
) -> AppliedRecord:
    """Aplica na porta de tarefa.

    `MOVE` e `UPDATE` caem no mesmo lugar de propósito: em tarefa os
    dois são escrita de campo (prazo ou lista num caso, título ou nota
    no outro). A distinção existe para a UI dar destaque diferente, não
    para o provedor.
    """
    match item.op:
        case Op.CREATE:
            task = item.task_after
            assert task is not None
            created = await tasks.create_task(replace(task, source="age"))
            return AppliedRecord(
                item_id=item.id,
                op=Op.CREATE,
                applied_ref=created.id,
                snapshot_before=None,
                target=Target.TASK,
            )

        case Op.MOVE | Op.UPDATE:
            before = item.task_before
            after = item.task_after
            assert before is not None and after is not None
            updated = await tasks.update_task(
                replace(after, id=before.id),
                expected_etag=before.etag,
            )
            return AppliedRecord(
                item_id=item.id,
                op=item.op,
                applied_ref=updated.id,
                snapshot_before=before,
                target=Target.TASK,
            )

        case Op.COMPLETE:
            before = item.task_before
            assert before is not None
            done = await tasks.complete_task(before.list_id, before.id or "")
            return AppliedRecord(
                item_id=item.id,
                op=Op.COMPLETE,
                applied_ref=done.id,
                snapshot_before=before,
                target=Target.TASK,
            )

        case Op.DELETE:
            before = item.task_before
            assert before is not None
            await tasks.delete_task(before.list_id, before.id or "")
            return AppliedRecord(
                item_id=item.id,
                op=Op.DELETE,
                applied_ref=before.id,
                snapshot_before=before,
                target=Target.TASK,
            )

        case Op.REORDER:
            list_id = item.target_ref or ""
            # Ordem atual, para poder desfazer. Lida ANTES de escrever,
            # porque depois dela não há como reconstruir.
            current = await tasks.list_tasks([list_id])
            previous = tuple(t.id or "" for t in current)
            await tasks.reorder(list_id, list(item.reorder_ids))
            return AppliedRecord(
                item_id=item.id,
                op=Op.REORDER,
                applied_ref=list_id,
                snapshot_before=None,
                target=Target.TASK,
                previous_order=previous,
            )

        case _:
            raise NotImplementedError(
                f"operação {item.op} não existe para tarefa"
            )


async def _revert(
    records: list[AppliedRecord],
    provider: CalendarProvider,
    tasks: TaskProvider | None = None,
) -> list[str]:
    """Reversão compensatória. Devolve o que NÃO conseguiu desfazer.

    Inverte na ordem contrária da aplicação. Cada falha é acumulada em
    vez de abortar: desfazer 3 de 4 é melhor que desfazer nenhum, e o
    usuário precisa saber exatamente o que sobrou.
    """
    unresolved: list[str] = []

    for record in reversed(records):
        try:
            if record.target == Target.TASK:
                if tasks is None:
                    unresolved.append(
                        f"não consegui desfazer {record.op.value} de tarefa: "
                        "a lista não está mais acessível"
                    )
                    continue
                note = await _revert_task(record, tasks)
                if note:
                    unresolved.append(note)
                continue

            match record.op:
                case Op.CREATE:
                    if record.applied_ref:
                        await provider.delete_event("", record.applied_ref)

                case Op.MOVE | Op.UPDATE:
                    if record.snapshot_before is not None:
                        await provider.update_event(record.snapshot_before)

                case Op.DELETE:
                    if record.snapshot_before is not None:
                        # O ID muda ao recriar, e isso é dito ao
                        # usuário: o link do evento antigo para de
                        # funcionar (§5.8).
                        recreated = await provider.create_event(
                            replace(record.snapshot_before, id=None)
                        )
                        unresolved.append(
                            f"'{record.snapshot_before.title}' foi recriado "
                            f"com um id novo ({recreated.id}); links antigos "
                            "para ele não funcionam mais"
                        )
        except Exception as exc:  # noqa: BLE001
            title = (
                getattr(record.snapshot_before, "title", None)
                or record.applied_ref
                or "item"
            )
            unresolved.append(
                f"não consegui desfazer {record.op.value} de '{title}': {exc}"
            )

    return unresolved


async def _revert_task(
    record: AppliedRecord, tasks: TaskProvider
) -> str | None:
    """Reversão compensatória na porta de tarefa.

    Devolve a pendência em texto, ou `None` se desfez por completo.
    """
    snapshot = record.snapshot_before
    match record.op:
        case Op.CREATE:
            if record.applied_ref:
                await tasks.delete_task("", record.applied_ref)
            return None

        case Op.MOVE | Op.UPDATE:
            if isinstance(snapshot, Task):
                await tasks.update_task(snapshot)
            return None

        case Op.COMPLETE:
            # Desfazer uma conclusão exige método próprio, e não todo
            # provedor tem. Quando não tem, o usuário precisa saber:
            # uma tarefa marcada como feita que não deveria estar é
            # pior que um erro visível, porque ela desaparece da lista.
            if isinstance(snapshot, Task) and snapshot.id:
                reopen = getattr(tasks, "reopen_task", None)
                if reopen is None:
                    return (
                        f"'{snapshot.title}' ficou marcada como concluída e "
                        f"o provedor não permite reabrir pela API — "
                        "desmarque no app da lista"
                    )
                await reopen(snapshot.list_id, snapshot.id)
            return None

        case Op.DELETE:
            if isinstance(snapshot, Task):
                restore = getattr(tasks, "restore_task", None)
                if restore is not None and snapshot.id:
                    await restore(snapshot.id)
                    return None
                # Sem restore: recriar é o melhor possível, e o id muda.
                recreated = await tasks.create_task(
                    replace(snapshot, id=None)
                )
                return (
                    f"'{snapshot.title}' foi recriada com um id novo "
                    f"({recreated.id}); vínculos antigos para ela não "
                    "funcionam mais"
                )
            return None

        case Op.REORDER:
            if record.previous_order and record.applied_ref:
                await tasks.reorder(
                    record.applied_ref, list(record.previous_order)
                )
            return None

    return None


async def _revalidate(
    items: list[DiffItem],
    provider: CalendarProvider,
    tasks: TaskProvider | None = None,
) -> list[str]:
    """Confere se o que vai ser mexido ainda está como na proposta.

    Relê a agenda E as listas (§5.8, passo 1). Dois casos distintos,
    com reações diferentes:
      - desapareceu → o modelo pode ter alucinado, ou alguém apagou.
      - etag divergente → alguém editou, e o usuário aprovou uma
        proposta feita sobre outro estado.

    Os dois abortam. Aplicar sobre um estado diferente do que o usuário
    viu ao aprovar é pior que pedir para ele revisar de novo.
    """
    problems: list[str] = []

    for item in items:
        event = item.event_before
        if event is not None and event.id is not None:
            found = await provider.list_events(
                [event.calendar_id], event.start, event.end
            )
            current = next((e for e in found if e.id == event.id), None)

            if current is None:
                problems.append(f"'{event.title}' não está mais na agenda")
            elif (
                provider.capabilities.supports_etag_concurrency
                and event.etag is not None
                and current.etag != event.etag
            ):
                problems.append(
                    f"'{event.title}' foi editado depois que eu propus"
                )
            continue

        task = item.task_before
        if task is not None and task.id is not None and tasks is not None:
            # `include_done=True` de propósito: se a tarefa foi
            # concluída entre a proposta e a aprovação, isso tem de
            # aparecer como conflito — e não como "desapareceu", que
            # mandaria o usuário procurar o que não se perdeu.
            found_tasks = await tasks.list_tasks(
                [task.list_id], include_done=True
            )
            current_task = next(
                (t for t in found_tasks if t.id == task.id), None
            )

            if current_task is None:
                problems.append(f"'{task.title}' não está mais na lista")
            elif current_task.is_done and not task.is_done:
                problems.append(
                    f"'{task.title}' já foi concluída depois que eu propus"
                )
            elif (
                tasks.capabilities.supports_etag_concurrency
                and task.etag is not None
                and current_task.etag != task.etag
            ):
                problems.append(
                    f"'{task.title}' foi editada depois que eu propus"
                )

    return problems


def _select_items(plan: Plan, item_ids: list[UUID] | None) -> list[DiffItem]:
    if item_ids is None:
        # Sem seleção explícita: tudo que não foi rejeitado.
        return [i for i in plan.items if i.status != ItemStatus.REJECTED]

    wanted = set(item_ids)
    # Aceitar um item arrasta os ligados a ele (§5.7.1).
    for item_id in list(wanted):
        for linked in plan.linked_group(item_id):
            wanted.add(linked.id)

    return [i for i in plan.items if i.id in wanted]


def _group_linked(plan: Plan, items: list[DiffItem]) -> list[list[DiffItem]]:
    """Agrupa itens ligados; cada grupo é aplicado ou revertido junto."""
    by_id = {i.id: i for i in items}
    seen: set[UUID] = set()
    groups: list[list[DiffItem]] = []

    for item in items:
        if item.id in seen:
            continue
        group = [
            member
            for member in plan.linked_group(item.id)
            if member.id in by_id
        ]
        if not group:
            group = [item]
        for member in group:
            seen.add(member.id)
        groups.append(group)

    return groups
