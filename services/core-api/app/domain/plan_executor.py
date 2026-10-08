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

log = logging.getLogger("core-api.plan_executor")


@dataclass
class AppliedRecord:
    """O que foi feito, para poder desfazer (§5.8)."""

    item_id: UUID
    op: Op
    applied_ref: str | None
    snapshot_before: Event | None


@dataclass
class ExecutionResult:
    status: PlanStatus
    applied: list[AppliedRecord] = field(default_factory=list)
    failed: list[tuple[UUID, str]] = field(default_factory=list)
    # Itens que a reversão também não conseguiu desfazer. É a lista que
    # o usuário recebe para arrumar à mão.
    manual_fix_needed: list[str] = field(default_factory=list)
    revalidation_conflicts: list[str] = field(default_factory=list)

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
    item_ids: list[UUID] | None = None,
    skip_revalidation: bool = False,
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

    # Bloqueio explícito: itens com conflito `blocking` não entram sem
    # confirmação separada.
    blocked = [i for i in selected if i.is_blocking]
    if blocked:
        return ExecutionResult(
            status=PlanStatus.FAILED,
            failed=[
                (
                    i.id,
                    "conflito bloqueante: "
                    + "; ".join(c.message for c in i.conflicts),
                )
                for i in blocked
            ],
        )

    if not skip_revalidation:
        conflicts = await _revalidate(selected, provider)
        if conflicts:
            raise RevalidationFailed(conflicts)

    result = ExecutionResult(status=PlanStatus.APPLIED)
    groups = _group_linked(plan, selected)

    for group in groups:
        group_applied: list[AppliedRecord] = []
        group_failed = False

        for item in ordered_for_apply(group):
            try:
                record = await _apply_item(item, provider)
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
            unresolved = await _revert(group_applied, provider)
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
    item: DiffItem, provider: CalendarProvider
) -> AppliedRecord:
    if item.target != Target.EVENT:
        raise NotImplementedError(
            f"target {item.target} chega no M4 (§5.7)"
        )

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


async def _revert(
    records: list[AppliedRecord], provider: CalendarProvider
) -> list[str]:
    """Reversão compensatória. Devolve o que NÃO conseguiu desfazer.

    Inverte na ordem contrária da aplicação. Cada falha é acumulada em
    vez de abortar: desfazer 3 de 4 é melhor que desfazer nenhum, e o
    usuário precisa saber exatamente o que sobrou.
    """
    unresolved: list[str] = []

    for record in reversed(records):
        try:
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
                record.snapshot_before.title
                if record.snapshot_before
                else record.applied_ref or "evento"
            )
            unresolved.append(
                f"não consegui desfazer {record.op.value} de '{title}': {exc}"
            )

    return unresolved


async def _revalidate(
    items: list[DiffItem], provider: CalendarProvider
) -> list[str]:
    """Confere se os eventos a mexer ainda estão como na proposta.

    Dois casos distintos, com reações diferentes:
      - evento desapareceu → o modelo pode ter alucinado, ou alguém
        apagou. Aborta.
      - etag divergente → alguém editou. Aborta, porque o usuário
        aprovou uma proposta feita sobre outro estado.
    """
    problems: list[str] = []

    for item in items:
        if item.before is None or item.before.id is None:
            continue

        found = await provider.list_events(
            [item.before.calendar_id],
            item.before.start,
            item.before.end,
        )
        current = next((e for e in found if e.id == item.before.id), None)

        if current is None:
            problems.append(
                f"'{item.before.title}' não está mais na agenda"
            )
            continue

        if (
            provider.capabilities.supports_etag_concurrency
            and item.before.etag is not None
            and current.etag != item.before.etag
        ):
            problems.append(
                f"'{item.before.title}' foi editado depois que eu propus"
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
