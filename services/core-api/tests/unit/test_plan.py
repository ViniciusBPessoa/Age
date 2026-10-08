"""Testes de PlanDiff e PlanExecutor.

ARQUITETURA.md §5.7, §5.8. O foco é nos caminhos de FALHA: aplicar
tudo com sucesso é o caso fácil. O que decide a confiança no produto é
o que acontece quando o terceiro de oito eventos falha.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.adapters.calendar.in_memory import InMemoryCalendar
from app.domain.plan_diff import (
    ConflictKind,
    Op,
    PlanBuilder,
    PlanStatus,
    ordered_for_apply,
    validate_plan,
)
from app.domain.plan_executor import (
    PlanExpired,
    RevalidationFailed,
    apply_plan,
)
from app.ports.calendar import (
    BusyInterval,
    Event,
    Flexibility,
)

SP = ZoneInfo("America/Sao_Paulo")
FUTURE = 2027  # longe o bastante para não virar passado


def ev(
    hour: int,
    *,
    title: str = "Estudo",
    day: int = 15,
    flex: Flexibility = Flexibility.FIXED,
    eid: str | None = None,
    etag: str | None = None,
    duration_h: int = 1,
) -> Event:
    start = datetime(FUTURE, 3, day, hour, tzinfo=SP)
    return Event(
        id=eid,
        calendar_id="primary",
        title=title,
        start=start,
        end=start + timedelta(hours=duration_h),
        flexibility=flex,
        etag=etag,
    )


def builder() -> PlanBuilder:
    return PlanBuilder(uuid4(), uuid4())


# ---------------------------------------------------------------
# DiffItem: invariantes
# ---------------------------------------------------------------
def test_reason_is_mandatory() -> None:
    """Mover sem explicar não é permitido pelo schema (§5.6)."""
    b = builder()
    with pytest.raises(ValueError, match="reason"):
        b.propose_create(ev(14), "")
    with pytest.raises(ValueError, match="reason"):
        b.propose_create(ev(14), "   ")


def test_create_requires_after() -> None:
    from app.domain.plan_diff import DiffItem, Target

    with pytest.raises(ValueError, match="CREATE exige"):
        DiffItem(
            id=uuid4(),
            target=Target.EVENT,
            op=Op.CREATE,
            reason="motivo",
            after=None,
        )


def test_move_requires_before_and_after() -> None:
    from app.domain.plan_diff import DiffItem, Target

    with pytest.raises(ValueError, match="exige"):
        DiffItem(
            id=uuid4(),
            target=Target.EVENT,
            op=Op.MOVE,
            reason="motivo",
            after=ev(14),
        )


# ---------------------------------------------------------------
# Ordenação (§5.8)
# ---------------------------------------------------------------
def test_apply_order_frees_before_occupying() -> None:
    """DELETE → MOVE → UPDATE → CREATE.

    Não é estética: criar antes de mover geraria conflito artificial
    com o próprio plano.
    """
    b = builder()
    b.propose_create(ev(14), "novo bloco")
    b.propose_move(
        ev(10, title="Academia", flex=Flexibility.FLEXIBLE, eid="e1"),
        ev(20, title="Academia", flex=Flexibility.FLEXIBLE, eid="e1"),
        "abre bloco de 2h",
    )
    b.propose_delete(
        ev(8, title="Cancelado", flex=Flexibility.FLEXIBLE, eid="e2"),
        "não vai acontecer",
    )
    b.propose_update(
        ev(16, eid="e3"), ev(16, title="Renomeado", eid="e3"), "nome melhor"
    )

    ops = [i.op for i in ordered_for_apply(b.build().items)]
    assert ops == [Op.DELETE, Op.MOVE, Op.UPDATE, Op.CREATE]


# ---------------------------------------------------------------
# Validação (§5.7)
# ---------------------------------------------------------------
def test_detects_overlap_with_existing() -> None:
    b = builder()
    b.propose_create(ev(14), "bloco livre")
    busy = [
        BusyInterval(
            start=datetime(FUTURE, 3, 15, 14, tzinfo=SP),
            end=datetime(FUTURE, 3, 15, 15, tzinfo=SP),
        )
    ]
    plan = validate_plan(b.build(), busy, [])
    assert ConflictKind.OVERLAP in {c.kind for c in plan.items[0].conflicts}


def test_move_does_not_conflict_with_itself() -> None:
    """O horário que o MOVE libera não deve contar como ocupado.

    Sem isso, todo MOVE reportaria conflito consigo mesmo.
    """
    b = builder()
    before = ev(10, title="Academia", flex=Flexibility.FLEXIBLE, eid="e1")
    after = replace(before, start=before.start, end=before.end)
    b.propose_move(before, after, "mesmo horário, só renomeando")

    busy = [BusyInterval(start=before.start, end=before.end)]
    plan = validate_plan(b.build(), busy, [])
    assert ConflictKind.OVERLAP not in {
        c.kind for c in plan.items[0].conflicts
    }


def test_moving_fixed_event_is_blocking() -> None:
    """Mexer em FIXED exige confirmação explícita (§5.6)."""
    b = builder()
    b.propose_move(
        ev(9, title="Consulta médica", flex=Flexibility.FIXED, eid="e9"),
        ev(15, title="Consulta médica", flex=Flexibility.FIXED, eid="e9"),
        "tentando abrir espaço",
    )
    plan = validate_plan(b.build(), [], [])
    item = plan.items[0]
    assert item.is_blocking
    assert ConflictKind.TOUCHES_FIXED_EVENT in {
        c.kind for c in item.conflicts
    }


def test_past_event_is_blocking() -> None:
    from app.domain.plan_diff import DiffItem, Target

    past = datetime(2020, 1, 1, 10, tzinfo=SP)
    item = DiffItem(
        id=uuid4(),
        target=Target.EVENT,
        op=Op.CREATE,
        reason="teste",
        after=Event(
            id=None,
            calendar_id="primary",
            title="No passado",
            start=past,
            end=past + timedelta(hours=1),
        ),
    )
    b = builder()
    plan = b.build()
    plan.items = [item]
    plan = validate_plan(plan, [], [])
    assert plan.items[0].is_blocking


def test_daily_limit_conflict() -> None:
    b = builder()
    for hour in (8, 10, 12, 14, 16, 18, 20):
        b.propose_create(ev(hour, title=f"Bloco {hour}"), "encaixe")
    plan = validate_plan(
        b.build(), [], [], max_hours_per_day=timedelta(hours=6)
    )
    assert any(
        ConflictKind.OVER_DAILY_LIMIT in {c.kind for c in i.conflicts}
        for i in plan.items
    )


# ---------------------------------------------------------------
# Expiração (§5.7)
# ---------------------------------------------------------------
async def test_expired_plan_is_refused() -> None:
    """Agenda muda; aprovar proposta velha causaria estrago."""
    b = builder()
    b.propose_create(ev(14), "bloco livre")
    plan = b.build()
    plan.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(PlanExpired):
        await apply_plan(plan, InMemoryCalendar())


# ---------------------------------------------------------------
# Aplicação
# ---------------------------------------------------------------
async def test_applies_create() -> None:
    provider = InMemoryCalendar()
    b = builder()
    b.propose_create(ev(14), "bloco livre mais longo")
    result = await apply_plan(b.build(), provider)

    assert result.ok
    assert len(result.applied) == 1

    events = await provider.list_events(
        ["primary"],
        datetime(FUTURE, 3, 15, 0, tzinfo=SP),
        datetime(FUTURE, 3, 16, 0, tzinfo=SP),
    )
    assert len(events) == 1
    # Marcado como criado pelo Age: é o que permite saber o que podemos
    # mexer depois (§6.3).
    assert events[0].source == "age"


async def test_applies_move() -> None:
    provider = InMemoryCalendar()
    original = await provider.create_event(
        ev(10, title="Academia", flex=Flexibility.FLEXIBLE)
    )

    b = builder()
    b.propose_move(
        original,
        replace(
            original,
            start=original.start + timedelta(hours=10),
            end=original.end + timedelta(hours=10),
        ),
        "abre bloco contíguo de 2h para estudo",
    )
    result = await apply_plan(b.build(), provider)
    assert result.ok

    events = await provider.list_events(
        ["primary"],
        datetime(FUTURE, 3, 15, 0, tzinfo=SP),
        datetime(FUTURE, 3, 16, 0, tzinfo=SP),
    )
    assert events[0].start.hour == 20


async def test_applies_delete() -> None:
    provider = InMemoryCalendar()
    created = await provider.create_event(
        ev(10, title="Cancelado", flex=Flexibility.FLEXIBLE)
    )

    b = builder()
    b.propose_delete(created, "não vai acontecer mais")
    result = await apply_plan(b.build(), provider)
    assert result.ok

    events = await provider.list_events(
        ["primary"],
        datetime(FUTURE, 3, 15, 0, tzinfo=SP),
        datetime(FUTURE, 3, 16, 0, tzinfo=SP),
    )
    assert events == []


async def test_granular_approval() -> None:
    """Aceitar 1 de 3 é válido (§8.1)."""
    provider = InMemoryCalendar()
    b = builder()
    first = b.propose_create(ev(14, title="Aceito"), "primeiro")
    b.propose_create(ev(16, title="Rejeitado"), "segundo")
    b.propose_create(ev(18, title="Também rejeitado"), "terceiro")

    result = await apply_plan(b.build(), provider, item_ids=[first.id])
    assert result.ok
    assert len(result.applied) == 1

    events = await provider.list_events(
        ["primary"],
        datetime(FUTURE, 3, 15, 0, tzinfo=SP),
        datetime(FUTURE, 3, 16, 0, tzinfo=SP),
    )
    assert len(events) == 1
    assert events[0].title == "Aceito"


async def test_blocking_conflict_prevents_apply() -> None:
    provider = InMemoryCalendar()
    b = builder()
    b.propose_move(
        ev(9, title="Prova", flex=Flexibility.FIXED, eid="e9"),
        ev(15, title="Prova", flex=Flexibility.FIXED, eid="e9"),
        "tentando mover",
    )
    plan = validate_plan(b.build(), [], [])
    result = await apply_plan(plan, provider)

    assert result.status == PlanStatus.FAILED
    assert result.failed


# ---------------------------------------------------------------
# Revalidação (§5.8)
# ---------------------------------------------------------------
async def test_revalidation_catches_deleted_event() -> None:
    """Evento que desapareceu aborta a aplicação.

    Pode ser alucinação do modelo ou alguém que apagou. Em ambos os
    casos, aplicar sobre estado diferente do aprovado é pior.
    """
    provider = InMemoryCalendar()
    created = await provider.create_event(
        ev(10, title="Vai sumir", flex=Flexibility.FLEXIBLE)
    )

    b = builder()
    b.propose_move(
        created,
        replace(
            created,
            start=created.start + timedelta(hours=2),
            end=created.end + timedelta(hours=2),
        ),
        "remanejando",
    )
    plan = b.build()

    # Alguém apaga antes de aplicarmos.
    await provider.delete_event("primary", created.id or "")

    with pytest.raises(RevalidationFailed) as exc:
        await apply_plan(plan, provider)
    assert "não está mais na agenda" in exc.value.conflicts[0]


async def test_revalidation_catches_edited_event() -> None:
    provider = InMemoryCalendar()
    created = await provider.create_event(
        ev(10, title="Original", flex=Flexibility.FLEXIBLE)
    )

    b = builder()
    b.propose_move(
        created,
        replace(
            created,
            start=created.start + timedelta(hours=2),
            end=created.end + timedelta(hours=2),
        ),
        "remanejando",
    )
    plan = b.build()

    # Alguém edita, mudando o etag.
    await provider.update_event(replace(created, title="Editado por outro"))

    with pytest.raises(RevalidationFailed) as exc:
        await apply_plan(plan, provider)
    assert "editado depois" in exc.value.conflicts[0]


# ---------------------------------------------------------------
# Falha parcial e reversão — o caminho que mais importa
# ---------------------------------------------------------------
class FailingCalendar(InMemoryCalendar):
    """Falha na N-ésima escrita, para exercitar a reversão."""

    def __init__(self, fail_on_create: int) -> None:
        super().__init__()
        self._creates = 0
        self._fail_on = fail_on_create

    async def create_event(self, event):  # type: ignore[override]
        self._creates += 1
        if self._creates == self._fail_on:
            raise RuntimeError("provedor recusou (simulado)")
        return await super().create_event(event)


async def test_partial_failure_reports_what_entered() -> None:
    """Itens independentes: o que deu certo fica.

    Falhar o par do relatório não impede o par do capítulo 2 (§5.8).
    """
    provider = FailingCalendar(fail_on_create=2)
    b = builder()
    b.propose_create(ev(10, title="Primeiro"), "ok")
    b.propose_create(ev(14, title="Segundo"), "vai falhar")
    b.propose_create(ev(16, title="Terceiro"), "ok")

    result = await apply_plan(b.build(), provider)

    assert result.status == PlanStatus.PARTIALLY_APPLIED
    assert len(result.applied) == 2
    assert len(result.failed) == 1


async def test_linked_group_reverts_together() -> None:
    """Par ligado falhando reverte inteiro (§5.7.1).

    Meio par é estado inconsistente: bloco na agenda sem tarefa, ou
    tarefa apontando para horário vazio.
    """
    provider = FailingCalendar(fail_on_create=2)
    b = builder()
    a = b.propose_create(ev(10, title="Parte A"), "primeira metade")
    c = b.propose_create(ev(14, title="Parte B"), "segunda metade")
    b.link(a.id, c.id)

    result = await apply_plan(b.build(), provider)

    assert result.failed
    # A primeira foi criada e depois revertida: nada sobrou.
    events = await provider.list_events(
        ["primary"],
        datetime(FUTURE, 3, 15, 0, tzinfo=SP),
        datetime(FUTURE, 3, 16, 0, tzinfo=SP),
    )
    assert events == [], "grupo ligado não deve ficar pela metade"


async def test_accepting_one_linked_item_drags_the_other() -> None:
    """Aceitar meio par não é opção (§5.7.1)."""
    provider = InMemoryCalendar()
    b = builder()
    a = b.propose_create(ev(10, title="Parte A"), "primeira")
    c = b.propose_create(ev(14, title="Parte B"), "segunda")
    b.link(a.id, c.id)

    result = await apply_plan(b.build(), provider, item_ids=[a.id])
    assert len(result.applied) == 2, "o item ligado deve ser arrastado"


async def test_revert_of_delete_warns_about_new_id() -> None:
    """Recriar o que foi apagado gera id novo, e isso é dito (§5.8).

    O link do evento antigo para de funcionar, e esconder isso seria
    mentir sobre o que aconteceu.
    """
    provider = InMemoryCalendar()
    original = await provider.create_event(
        ev(10, title="Será apagado", flex=Flexibility.FLEXIBLE)
    )

    from app.domain.plan_executor import AppliedRecord, _revert

    unresolved = await _revert(
        [
            AppliedRecord(
                item_id=uuid4(),
                op=Op.DELETE,
                applied_ref=original.id,
                snapshot_before=original,
            )
        ],
        provider,
    )
    assert unresolved
    assert "id novo" in unresolved[0]


async def test_revert_failure_is_reported_not_swallowed() -> None:
    """Se a reversão falha, o usuário recebe a lista literal (§5.8)."""

    class UndeletableCalendar(InMemoryCalendar):
        async def delete_event(self, calendar_id, event_id, expected_etag=None):
            raise RuntimeError("não consigo apagar (simulado)")

    provider = UndeletableCalendar()

    from app.domain.plan_executor import AppliedRecord, _revert

    unresolved = await _revert(
        [
            AppliedRecord(
                item_id=uuid4(),
                op=Op.CREATE,
                applied_ref="algum-id",
                snapshot_before=None,
            )
        ],
        provider,
    )
    assert unresolved
    assert "não consegui desfazer" in unresolved[0]


async def test_empty_selection_is_rejection() -> None:
    provider = InMemoryCalendar()
    b = builder()
    b.propose_create(ev(14), "bloco")
    result = await apply_plan(b.build(), provider, item_ids=[])
    assert result.status == PlanStatus.REJECTED


# ---------------------------------------------------------------
# Regressão: sobrepor evento FIXED
# ---------------------------------------------------------------
def test_creating_over_fixed_event_is_blocking() -> None:
    """Agendar estudo em cima de uma prova não passa por descuido.

    Regressão: `fixed_events` era recebido, convertido em intervalos e
    descartado sem uso. O efeito era que MOVER um FIXED era detectado,
    mas CRIAR algo em cima dele não — justamente o caso mais provável
    quando o modelo propõe um bloco de estudo.
    """
    b = builder()
    b.propose_create(ev(14, title="Estudo de Álgebra"), "bloco livre")

    prova = ev(14, title="Prova de Cálculo", flex=Flexibility.FIXED, eid="p1")
    plan = validate_plan(
        b.build(),
        [BusyInterval(start=prova.start, end=prova.end)],
        [prova],
    )

    item = plan.items[0]
    assert item.is_blocking, "sobrepor FIXED deve bloquear"
    assert ConflictKind.TOUCHES_FIXED_EVENT in {c.kind for c in item.conflicts}
    # A mensagem diz COM O QUE choca: "choca com algo das 14h" é inútil.
    assert "Prova de Cálculo" in item.conflicts[0].message


def test_creating_over_flexible_event_is_only_warning() -> None:
    """Sobrepor algo flexível é alerta, não bloqueio.

    O usuário pode ter motivo para sobrepor a academia; decidir por ele
    seria o sistema passando dos limites.
    """
    b = builder()
    b.propose_create(ev(19, title="Estudo"), "único horário livre")

    academia = ev(19, title="Academia", flex=Flexibility.FLEXIBLE, eid="a1")
    plan = validate_plan(
        b.build(),
        [BusyInterval(start=academia.start, end=academia.end)],
        [academia],  # não é FIXED, então não entra como span intocável
    )

    item = plan.items[0]
    assert not item.is_blocking
    assert ConflictKind.OVERLAP in {c.kind for c in item.conflicts}
