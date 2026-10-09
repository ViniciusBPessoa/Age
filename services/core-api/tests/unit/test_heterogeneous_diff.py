"""Testes do diff heterogêneo e do par ligado.

ARQUITETURA.md §5.7 e §5.7.1. Este é o arquivo que cobre o que o M4
acrescenta de mais arriscado: um plano com itens de DOIS tipos, que vão
para portas diferentes, e que às vezes precisam ser aprovados ou
revertidos juntos.

O risco não é cada operação isolada — é a combinação. Um par ligado
aplicado pela metade deixa bloco na agenda sem tarefa, ou tarefa
apontando para horário vazio, e nenhum teste de uma porta só pega isso.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.domain.plan_diff import (
    ConflictKind,
    DiffItem,
    Op,
    PlanBuilder,
    Target,
    ordered_for_apply,
    validate_plan,
)
from app.ports.calendar import Event, Flexibility
from app.ports.tasks import Priority, Task, TaskStatus

SP = ZoneInfo("America/Sao_Paulo")


def a_task(
    *,
    title: str = "Relatório do TCC",
    task_id: str | None = "t1",
    due_date: date | None = None,
    due_at: datetime | None = None,
    **extra,
) -> Task:
    return Task(
        id=task_id,
        list_id="default",
        title=title,
        due_date=due_date,
        due_at=due_at,
        **extra,
    )


def an_event(
    *,
    title: str = "Relatório do TCC",
    day: int = 15,
    hour: int = 14,
    hours: int = 3,
    event_id: str | None = None,
) -> Event:
    start = datetime(2026, 10, day, hour, tzinfo=SP)
    return Event(
        id=event_id,
        calendar_id="primary",
        title=title,
        start=start,
        end=start + timedelta(hours=hours),
        flexibility=Flexibility.FLEXIBLE,
    )


def a_builder() -> PlanBuilder:
    return PlanBuilder(user_id=uuid4(), conversation_id=uuid4())


# ---------------------------------------------------------------
# Tipo e destino têm de combinar
# ---------------------------------------------------------------
def test_task_target_rejects_event_payload() -> None:
    """Um item TASK com `Event` dentro passaria pela validação de
    agenda e só explodiria na aplicação — longe da causa."""
    with pytest.raises(ValueError, match="deveria ser Task"):
        DiffItem(
            id=uuid4(),
            target=Target.TASK,
            op=Op.CREATE,
            reason="teste",
            after=an_event(),
        )


def test_event_target_rejects_task_payload() -> None:
    with pytest.raises(ValueError, match="deveria ser Event"):
        DiffItem(
            id=uuid4(),
            target=Target.EVENT,
            op=Op.CREATE,
            reason="teste",
            after=a_task(),
        )


def test_complete_is_rejected_for_events() -> None:
    """Evento não tem estado de conclusão (§3.3)."""
    with pytest.raises(ValueError, match="só existe para tarefa"):
        DiffItem(
            id=uuid4(),
            target=Target.EVENT,
            op=Op.COMPLETE,
            reason="teste",
            before=an_event(event_id="e1"),
        )


def test_reorder_requires_ids() -> None:
    with pytest.raises(ValueError, match="reorder_ids"):
        DiffItem(
            id=uuid4(),
            target=Target.TASK,
            op=Op.REORDER,
            reason="teste",
            before=a_task(),
            after=a_task(),
        )


# ---------------------------------------------------------------
# Tarefa não ocupa agenda
# ---------------------------------------------------------------
def test_task_with_due_time_does_not_occupy_the_calendar() -> None:
    """A distinção que justifica duas portas (§3.3).

    "Ligar pro dentista sexta às 15h" tem hora, mas não bloqueia a
    agenda — dá para ter três coisas com esse prazo sem conflito. Se
    prazo contasse como ocupação, o `Scheduler` recusaria horários
    livres e a agenda viveria cheia de conflitos falsos.
    """
    builder = a_builder()
    item = builder.propose_task_create(
        a_task(
            title="Ligar pro dentista",
            task_id=None,
            due_at=datetime(2026, 10, 16, 15, tzinfo=SP),
        ),
        "você pediu pra lembrar",
    )
    assert item.occupies is None
    assert item.frees is None


def test_event_still_occupies() -> None:
    builder = a_builder()
    item = builder.propose_create(an_event(), "bloco de trabalho")
    occupied = item.occupies
    assert occupied is not None
    assert occupied.end - occupied.start == timedelta(hours=3)


def test_many_tasks_same_deadline_produce_no_overlap_conflict() -> None:
    """Três tarefas com o mesmo prazo não conflitam entre si."""
    builder = a_builder()
    deadline = datetime(2026, 10, 16, 15, tzinfo=SP)
    for title in ("Pagar IPVA", "Revisar capítulo", "Responder orientador"):
        builder.propose_task_create(
            a_task(title=title, task_id=None, due_at=deadline),
            "prazo que você deu",
        )

    plan = validate_plan(
        builder.build(), [], [], now=datetime(2026, 10, 1, tzinfo=SP)
    )
    for item in plan.items:
        kinds = {c.kind for c in item.conflicts}
        assert ConflictKind.OVERLAP not in kinds


# ---------------------------------------------------------------
# O par ligado (§5.7.1)
# ---------------------------------------------------------------
def test_propose_time_for_task_creates_a_linked_pair() -> None:
    builder = a_builder()
    task = a_task(due_date=date(2026, 10, 20))
    block = an_event(day=15, hour=14, hours=3)

    event_item, task_item = builder.propose_time_for_task(
        task, block, reason="quinta é seu dia mais livre"
    )

    assert event_item.target == Target.EVENT
    assert task_item.target == Target.TASK
    # O vínculo é mútuo: aceitar por qualquer ponta arrasta o outro.
    assert task_item.id in event_item.links
    assert event_item.id in task_item.links


def test_linked_pair_makes_the_task_point_at_the_block() -> None:
    """Sem isso, o Age não saberia dizer "3h reservadas quinta"."""
    builder = a_builder()
    block = an_event(day=15, hour=14)
    _, task_item = builder.propose_time_for_task(
        a_task(due_date=date(2026, 10, 20)),
        block,
        reason="encaixe",
    )
    updated = task_item.task_after
    assert updated is not None
    assert updated.due_at == block.start


def test_linked_group_returns_both_sides() -> None:
    builder = a_builder()
    event_item, task_item = builder.propose_time_for_task(
        a_task(), an_event(), reason="encaixe"
    )
    plan = builder.build()

    group_ids = {i.id for i in plan.linked_group(event_item.id)}
    assert group_ids == {event_item.id, task_item.id}
    # E pela outra ponta, igual.
    assert {i.id for i in plan.linked_group(task_item.id)} == group_ids


def test_block_after_task_deadline_is_blocking() -> None:
    """O conflito que só existe porque as duas portas convivem.

    Um bloco reservado DEPOIS do prazo da tarefa a que está ligado é um
    plano que parece certo item por item e está errado como conjunto.
    Ninguém revisando dois cartões separados perceberia — por isso é
    bloqueante, não alerta.
    """
    builder = a_builder()
    task = a_task(due_at=datetime(2026, 10, 10, 18, tzinfo=SP))
    late_block = an_event(day=12, hour=14)  # dois dias DEPOIS do prazo

    builder.propose_time_for_task(task, late_block, reason="encaixe ruim")
    plan = validate_plan(
        builder.build(), [], [], now=datetime(2026, 10, 1, tzinfo=SP)
    )

    task_item = next(i for i in plan.items if i.target == Target.TASK)
    kinds = {c.kind for c in task_item.conflicts}
    assert ConflictKind.BLOCK_AFTER_DUE in kinds
    assert task_item.is_blocking


def test_block_on_the_due_date_itself_is_fine() -> None:
    """"Até sexta" é sexta INTEIRA.

    Comparar com 00:00 acusaria conflito em qualquer bloco marcado no
    próprio dia do prazo — que é justamente o dia mais provável.
    """
    builder = a_builder()
    task = a_task(due_date=date(2026, 10, 15))
    block = an_event(day=15, hour=14)

    builder.propose_time_for_task(task, block, reason="no dia do prazo")
    plan = validate_plan(
        builder.build(), [], [], now=datetime(2026, 10, 1, tzinfo=SP)
    )

    for item in plan.items:
        kinds = {c.kind for c in item.conflicts}
        assert ConflictKind.BLOCK_AFTER_DUE not in kinds


# ---------------------------------------------------------------
# Prazo vencido
# ---------------------------------------------------------------
def test_past_due_date_warns_but_does_not_block() -> None:
    """As 4 atrasadas de §5.7.3 precisam poder ser repactuadas.

    Bloquear impediria exatamente a operação que resolve o problema.
    """
    builder = a_builder()
    builder.propose_task_create(
        a_task(task_id=None, due_date=date(2026, 9, 1)),
        "tarefa atrasada que você mencionou",
    )
    plan = validate_plan(
        builder.build(), [], [], now=datetime(2026, 10, 1, tzinfo=SP)
    )
    item = plan.items[0]
    assert ConflictKind.DUE_DATE_PASSED in {c.kind for c in item.conflicts}
    assert not item.is_blocking


# ---------------------------------------------------------------
# Degradação aparece no cartão (§3.3.2)
# ---------------------------------------------------------------
def test_dropped_fields_become_a_visible_conflict() -> None:
    """O usuário tem de saber ANTES de aprovar.

    Descobrir depois, abrindo o app do provedor, é a experiência que
    §3.3.2 existe para evitar.
    """
    builder = a_builder()
    degraded = replace(
        a_task(task_id=None, due_date=date(2026, 10, 20)),
        dropped_fields=("due_at", "priority"),
    )
    builder.propose_task_create(degraded, "você pediu pra anotar")

    plan = validate_plan(
        builder.build(), [], [], now=datetime(2026, 10, 1, tzinfo=SP)
    )
    conflict = next(
        c
        for c in plan.items[0].conflicts
        if c.kind == ConflictKind.PROVIDER_WONT_STORE
    )
    assert "hora do prazo" in conflict.message
    assert "prioridade" in conflict.message
    assert conflict.severity == "warning"


# ---------------------------------------------------------------
# Ordem de aplicação (§5.8)
# ---------------------------------------------------------------
def test_task_is_applied_before_the_linked_event() -> None:
    """O evento aponta para a tarefa, então ela precisa existir antes."""
    builder = a_builder()
    event_item, task_item = builder.propose_time_for_task(
        a_task(), an_event(), reason="encaixe"
    )

    ordered = ordered_for_apply([event_item, task_item])
    # Ambos são escrita; dentro do par, TASK primeiro.
    positions = {i.target: n for n, i in enumerate(ordered)}
    assert positions[Target.TASK] < positions[Target.EVENT]


def test_delete_comes_before_create_across_targets() -> None:
    """Libera espaço antes de ocupar, mesmo com destinos misturados."""
    builder = a_builder()
    create_event = builder.propose_create(an_event(), "novo bloco")
    delete_task = builder.propose_task_delete(
        a_task(title="tarefa velha"), "você pediu pra remover"
    )

    ordered = ordered_for_apply([create_event, delete_task])
    assert ordered[0].op == Op.DELETE
    assert ordered[-1].op == Op.CREATE


def test_reorder_sorts_with_moves() -> None:
    builder = a_builder()
    reorder = builder.propose_task_reorder(
        "default",
        [a_task(task_id="t1"), a_task(task_id="t2")],
        "organizei por prazo",
    )
    create = builder.propose_create(an_event(), "bloco novo")

    ordered = ordered_for_apply([create, reorder])
    assert ordered[0].op == Op.REORDER


# ---------------------------------------------------------------
# A entidade Task
# ---------------------------------------------------------------
def test_due_at_normalizes_due_date() -> None:
    """Coerência resolvida na entidade, não em cada leitor.

    Sem isso, a lista ordenada por data e a timeline discordariam.
    """
    task = a_task(
        task_id=None,
        due_at=datetime(2026, 10, 16, 15, tzinfo=SP),
        due_date=date(2020, 1, 1),  # errado de propósito
    )
    assert task.due_date == date(2026, 10, 16)


def test_naive_due_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        a_task(task_id=None, due_at=datetime(2026, 10, 16, 15))


def test_done_without_timestamp_is_rejected() -> None:
    """Sem `completed_at` não há relatório de §5.9.9."""
    with pytest.raises(ValueError, match="completed_at"):
        Task(
            id="t1",
            list_id="default",
            title="Pagar IPVA",
            status=TaskStatus.DONE,
        )


def test_date_only_task_is_overdue_at_end_of_day() -> None:
    """"Até sexta" não vence às 00:00 de sexta.

    Tratar como 00:00 marcaria como atrasado tudo que ainda dá tempo de
    fazer, que é o erro que faz o usuário desconfiar da lista.
    """
    task = a_task(task_id=None, due_date=date(2026, 10, 16))
    morning = datetime(2026, 10, 16, 9, tzinfo=SP)
    next_day = datetime(2026, 10, 17, 9, tzinfo=SP)

    assert not task.is_overdue(morning)
    assert task.is_overdue(next_day)


def test_done_task_is_never_overdue() -> None:
    task = a_task(
        task_id=None,
        due_date=date(2020, 1, 1),
        status=TaskStatus.DONE,
        completed_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    assert not task.is_overdue(datetime(2026, 10, 1, tzinfo=SP))


def test_priority_ranks_are_ordered() -> None:
    assert Priority.LOW.rank < Priority.NORMAL.rank
    assert Priority.NORMAL.rank < Priority.HIGH.rank
    assert Priority.HIGH.rank < Priority.URGENT.rank

# ---------------------------------------------------------------
# Serialização para a revisão (§8.1)
# ---------------------------------------------------------------
def test_plan_serialization_handles_both_targets() -> None:
    """`GET /plans/{id}` não pode quebrar num item de tarefa.

    O bug que este teste existe para impedir foi real e passou pelo M4
    inteiro: `_to_out` em `api/plans.py` tinha um `event_dict` que lia
    `event.start`, e um `Task` não tem `start`. Resultado: **500 em
    qualquer plano com item de tarefa** — inclusive o par ligado de
    §5.7.1, que é o caso central do M4.

    Por que a suíte do M4 não pegou: ela monta planos pelo
    `PlanBuilder`, valida pelo `validate_plan` e aplica pelo
    `PlanExecutor`, sem passar por esta rota. E o smoke lia o plano
    direto do banco. A rota de leitura ficou sem cobertura justamente
    porque todo o resto tinha — quem achou foi a tela de revisão, o
    primeiro código a buscar o diff pela API.

    O teste chama a função de serialização de verdade, e não uma cópia:
    a cópia passaria enquanto a original quebrasse.
    """
    from app.api.plans import _to_out

    builder = a_builder()
    event_item = builder.propose_create(an_event(), "bloco para a tarefa")
    task_item = builder.propose_task_update(
        a_task(due_date=date(2026, 10, 20)),
        a_task(
            due_at=datetime(2026, 10, 15, 14, tzinfo=SP),
            priority=Priority.HIGH,
            estimated=timedelta(minutes=180),
        ),
        "aponta para o bloco reservado",
    )
    # O par ligado: aprovado e revertido como unidade (§5.7.1).
    builder.link(event_item.id, task_item.id)

    out = _to_out(builder.build())

    assert len(out.items) == 2

    serialized_event = next(i for i in out.items if i.target == "event")
    assert serialized_event.after is not None
    assert serialized_event.after["_kind"] == "event"
    assert serialized_event.after["start"].startswith("2026-10-15T14:00")

    serialized_task = next(i for i in out.items if i.target == "task")
    assert serialized_task.after is not None
    assert serialized_task.after["_kind"] == "task"
    # Tarefa carrega PRAZO, não intervalo: §5.7 é explícito que prazo
    # não ocupa a agenda, e mandar `start`/`end` faria a UI desenhar
    # uma ocupação inexistente.
    assert "start" not in serialized_task.after
    assert serialized_task.after["due_at"].startswith("2026-10-15T14:00")
    assert serialized_task.after["estimated_min"] == 180
    assert serialized_task.after["priority"] == "high"
    # O `before` também: um item de UPDATE mostra os dois lados, e o
    # cartão do diff precisa do prazo anterior para dizer o que muda.
    assert serialized_task.before is not None
    assert serialized_task.before["due_date"] == "2026-10-20"
    # O vínculo chega à UI, que é o que permite tratar o par como um.
    assert serialized_task.links and serialized_event.links


def test_plan_serialization_survives_task_without_due() -> None:
    """Tarefa sem prazo nenhum também serializa.

    "Comprar presente · sem prazo" é um item legítimo de §8.1, e
    `due_date`/`due_at` nulos não podem virar `AttributeError` no
    `.isoformat()`.
    """
    from app.api.plans import _to_out

    builder = a_builder()
    builder.propose_task_create(
        a_task(title="Comprar presente", task_id=None), "você pediu"
    )

    out = _to_out(builder.build())
    assert out.items[0].after is not None
    assert out.items[0].after["due_at"] is None
    assert out.items[0].after["due_date"] is None
    assert out.items[0].after["estimated_min"] is None
