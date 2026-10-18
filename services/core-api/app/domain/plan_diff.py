"""Proposta de mudanças, ainda não aplicada.

ARQUITETURA.md §5.7. O `PlanDiff` é o que torna a escrita segura: as
tools de escrita do modelo **não executam nada**, só acumulam itens
aqui. Nada toca a agenda antes de o usuário aprovar.

Isso é a única defesa sólida contra duas falhas que vão acontecer:
alucinação do modelo e prompt injection via convite de calendário
(§9.4). Uma injeção bem-sucedida consegue, no máximo, propor algo
estranho — que o usuário vê no diff e rejeita.

Núcleo puro: nada de I/O. A persistência é do repositório; a aplicação é
do `PlanExecutor`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from app.ports.calendar import BusyInterval, Event, Flexibility
from app.ports.tasks import Task


class Target(StrEnum):
    """Qual porta recebe o item (§5.7).

    O diff é **heterogêneo**: um plano pode conter itens dos dois tipos,
    aprovados em bloco ou um a um. "Marca dentista quinta 15h e põe na
    lista comprar presente" é um plano só, com dois destinos.
    """

    EVENT = "event"
    TASK = "task"


class Op(StrEnum):
    CREATE = "CREATE"
    MOVE = "MOVE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    # Só para tarefa: evento não tem estado de conclusão nem posição
    # numa lista (§3.3).
    COMPLETE = "COMPLETE"
    REORDER = "REORDER"


# `MOVE` tem significado diferente em cada destino, e isso está aqui
# para quem lê o enum não supor que é a mesma coisa: em evento é mudar
# HORÁRIO; em tarefa é mudar PRAZO ou LISTA (§5.7).
_TASK_ONLY_OPS = frozenset({Op.COMPLETE, Op.REORDER})


class ItemStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    PARTIALLY_APPLIED = "partially_applied"
    APPLIED = "applied"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class ConflictKind(StrEnum):
    OVERLAP = "overlap"
    OVER_DAILY_LIMIT = "over_daily_limit"
    OUTSIDE_WORKING_HOURS = "outside_working_hours"
    TOUCHES_FIXED_EVENT = "touches_fixed_event"
    PAST = "past"
    AFTER_DEADLINE = "after_deadline"
    # Tarefa (§5.7.3)
    DUE_DATE_PASSED = "due_date_passed"
    BLOCK_AFTER_DUE = "block_after_due"
    PROVIDER_WONT_STORE = "provider_wont_store"


@dataclass(frozen=True)
class Conflict:
    kind: ConflictKind
    message: str
    # Alerta destacado na UI em vez de bloqueio: o usuário decide.
    severity: str = "warning"  # warning | blocking


@dataclass(frozen=True)
class DiffItem:
    """Uma mudança proposta.

    `reason` é obrigatório e não tem default: mover algo sem explicar
    não é permitido pelo schema (§5.6). A justificativa vem do
    Scheduler, não do modelo, para corresponder ao cálculo real.
    """

    id: UUID
    target: Target
    op: Op
    reason: str
    target_ref: str | None = None  # id externo; None para CREATE
    # `Event` ou `Task`, conforme `target`. Não há tipo unificado de
    # propósito: tarefa e evento são entidades diferentes (§3.3), e um
    # `Union` honesto obriga quem consome a olhar o `target` antes de
    # assumir que existe `.start`. Os acessores `as_event`/`as_task`
    # concentram essa checagem.
    before: Event | Task | None = None
    after: Event | Task | None = None
    confidence: float = 1.0
    conflicts: tuple[Conflict, ...] = ()
    # Ids de outros itens do mesmo plano que formam unidade (§5.7.1).
    links: tuple[UUID, ...] = ()
    # Só para `REORDER`: a ordem pedida, por id de tarefa. Fica fora de
    # `after` porque reordenar não muda nenhum campo da tarefa — muda a
    # relação entre elas, que não cabe numa entidade só.
    reorder_ids: tuple[str, ...] = ()
    sequence: int = 0
    status: ItemStatus = ItemStatus.PENDING

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"item {self.op}: `reason` é obrigatório — mudança sem "
                "justificativa não é permitida (§5.6)"
            )
        if self.op == Op.CREATE and self.after is None:
            raise ValueError("CREATE exige `after`")
        if self.op in (Op.MOVE, Op.UPDATE) and (
            self.before is None or self.after is None
        ):
            raise ValueError(f"{self.op} exige `before` e `after`")
        if self.op == Op.DELETE and self.before is None:
            raise ValueError("DELETE exige `before`")
        if self.op == Op.COMPLETE and self.before is None:
            raise ValueError("COMPLETE exige `before`")
        if self.op == Op.REORDER and not self.reorder_ids:
            raise ValueError("REORDER exige `reorder_ids`")

        # Operação só-de-tarefa num evento é erro de programação, não
        # entrada ruim do modelo: `COMPLETE` de evento não existe porque
        # evento não tem estado de conclusão (§3.3). Falhar aqui, na
        # construção, evita o item chegar ao executor e virar
        # `NotImplementedError` no meio de uma aplicação parcial.
        if self.op in _TASK_ONLY_OPS and self.target != Target.TASK:
            raise ValueError(
                f"{self.op} só existe para tarefa; evento não tem "
                "estado de conclusão nem posição em lista (§3.3)"
            )

        # E o inverso: o payload tem de combinar com o destino. Um item
        # `Target.TASK` carregando `Event` passaria pela validação de
        # conflito de agenda e só explodiria na aplicação.
        for slot, value in (("before", self.before), ("after", self.after)):
            if value is None:
                continue
            expected = Task if self.target == Target.TASK else Event
            if not isinstance(value, expected):
                raise ValueError(
                    f"item {self.op} com target={self.target.value}: "
                    f"`{slot}` deveria ser {expected.__name__}, "
                    f"veio {type(value).__name__}"
                )

    @property
    def is_blocking(self) -> bool:
        return any(c.severity == "blocking" for c in self.conflicts)

    @property
    def is_task(self) -> bool:
        return self.target == Target.TASK

    # Acessores tipados. Existem para que o resto do código não espalhe
    # `isinstance` nem assuma `.start` num item de tarefa — que era o
    # jeito mais provável de o diff heterogêneo quebrar em produção.
    @property
    def event_before(self) -> Event | None:
        return self.before if isinstance(self.before, Event) else None

    @property
    def event_after(self) -> Event | None:
        return self.after if isinstance(self.after, Event) else None

    @property
    def task_before(self) -> Task | None:
        return self.before if isinstance(self.before, Task) else None

    @property
    def task_after(self) -> Task | None:
        return self.after if isinstance(self.after, Task) else None

    @property
    def title(self) -> str:
        """Título do que está sendo mexido, para mensagem ao usuário."""
        subject = self.after if self.after is not None else self.before
        return getattr(subject, "title", "(sem título)")

    @property
    def occupies(self) -> BusyInterval | None:
        """Intervalo que este item passa a ocupar, se houver.

        Tarefa devolve `None` **mesmo quando tem `due_at`**, e essa é a
        distinção que justifica duas portas (§3.3): "ligar pro dentista
        sexta às 15h" tem hora, mas não bloqueia a agenda — dá para ter
        três coisas marcadas para as 15h de sexta sem conflito nenhum.
        Tratar prazo como ocupação encheria a agenda de conflitos
        falsos e faria o `Scheduler` recusar horários livres.

        Quem quer que a tarefa ocupe tempo pede um bloco, e aí há DOIS
        itens ligados — o evento é que ocupa (§5.7.1).
        """
        event = self.event_after
        if event is None:
            return None
        return BusyInterval(start=event.start, end=event.end)

    @property
    def frees(self) -> BusyInterval | None:
        """Intervalo que este item libera, se houver."""
        event = self.event_before
        if event is None:
            return None
        if self.op in (Op.MOVE, Op.DELETE):
            return BusyInterval(start=event.start, end=event.end)
        return None


# Janela padrão de validade. Agenda muda; aprovar proposta de três dias
# atrás causaria estrago (§5.7).
DEFAULT_TTL = timedelta(hours=24)

# Folga antes de declarar um horário "já passou".
#
# A validação roda na LEITURA, com `reference = agora`, e entre o Age
# propor e a pessoa clicar passa tempo real: a promoção de rascunho
# para plano é preguiçosa e já foi medida em 98 segundos na conta de
# verdade, e ainda há o tempo de ler o cartão e decidir.
#
# Sem esta folga, o pedido mais natural que existe ("bloqueia a próxima
# hora pra eu estudar") nasce bloqueado por alguns minutos de atraso, e
# a pessoa teria de confirmar conflito em plano nenhum conflito tem.
# Dez minutos é o suficiente para ler e decidir; além disso a recusa é
# honesta.
PAST_GRACE = timedelta(minutes=10)


@dataclass
class Plan:
    id: UUID
    user_id: UUID
    conversation_id: UUID | None
    items: list[DiffItem] = field(default_factory=list)
    summary: str = ""
    status: PlanStatus = PlanStatus.PROPOSED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + DEFAULT_TTL
    )
    origin_note: str | None = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at

    @property
    def accepted_items(self) -> list[DiffItem]:
        return [i for i in self.items if i.status == ItemStatus.ACCEPTED]

    def item(self, item_id: UUID) -> DiffItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def linked_group(self, item_id: UUID) -> list[DiffItem]:
        """Itens que formam unidade com este (§5.7.1).

        Aceitar um e rejeitar o outro deixaria o sistema inconsistente —
        bloco na agenda sem tarefa, ou tarefa apontando para horário
        vazio. A UI trata o grupo como uma coisa só.
        """
        target = self.item(item_id)
        if target is None:
            return []
        if not target.links:
            return [target]

        group = {target.id: target}
        for linked_id in target.links:
            found = self.item(linked_id)
            if found is not None:
                group[found.id] = found
        return list(group.values())


# ---------------------------------------------------------------
# Construção
# ---------------------------------------------------------------
class PlanBuilder:
    """Acumula itens durante o laço do orquestrador.

    As tools de escrita do modelo chamam estes métodos, que devolvem
    `{"staged": true, "diff_item_id": ...}`. O modelo sabe que registrou
    a intenção, não que executou — e o system prompt diz isso
    explicitamente, para ele não afirmar "já agendei" (§5.2).
    """

    def __init__(self, user_id: UUID, conversation_id: UUID | None) -> None:
        self._plan = Plan(
            id=uuid4(), user_id=user_id, conversation_id=conversation_id
        )
        self._next_sequence = 0

    def propose_create(self, event: Event, reason: str) -> DiffItem:
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.CREATE,
                reason=reason,
                after=event,
                sequence=self._take_sequence(),
            )
        )

    def propose_move(
        self, before: Event, after: Event, reason: str
    ) -> DiffItem:
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.MOVE,
                reason=reason,
                target_ref=before.id,
                before=before,
                after=after,
                sequence=self._take_sequence(),
            )
        )

    def propose_update(
        self, before: Event, after: Event, reason: str
    ) -> DiffItem:
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.UPDATE,
                reason=reason,
                target_ref=before.id,
                before=before,
                after=after,
                sequence=self._take_sequence(),
            )
        )

    def propose_delete(self, before: Event, reason: str) -> DiffItem:
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.DELETE,
                reason=reason,
                target_ref=before.id,
                before=before,
                sequence=self._take_sequence(),
            )
        )

    # -----------------------------------------------------------
    # Tarefa (§5.7)
    # -----------------------------------------------------------
    def propose_task_create(self, task: Task, reason: str) -> DiffItem:
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.CREATE,
                reason=reason,
                after=task,
                sequence=self._take_sequence(),
            )
        )

    def propose_task_update(
        self, before: Task, after: Task, reason: str
    ) -> DiffItem:
        """Mudança de campo da tarefa: título, notas, prioridade, prazo.

        `MOVE` de tarefa é mudança de PRAZO ou de LISTA e tem método
        próprio, porque a UI mostra as duas coisas de forma diferente —
        mudar o prazo de alguém precisa de destaque, mudar a nota não.
        """
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.UPDATE,
                reason=reason,
                target_ref=before.id,
                before=before,
                after=after,
                sequence=self._take_sequence(),
            )
        )

    def propose_task_move(
        self, before: Task, after: Task, reason: str
    ) -> DiffItem:
        """Repactuação de prazo, ou mudança de lista (§5.7)."""
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.MOVE,
                reason=reason,
                target_ref=before.id,
                before=before,
                after=after,
                sequence=self._take_sequence(),
            )
        )

    def propose_task_complete(self, before: Task, reason: str) -> DiffItem:
        """Concluir — e SEMPRE por pedido explícito (§5.7.3).

        A regra mais importante da organização em massa: **nunca
        concluir tarefa por inferência.** "Você comprou o cabo?" é
        pergunta, nunca `COMPLETE` automático. Concluir algo que a
        pessoa não fez destrói a confiança na lista, e uma lista em que
        não se confia não é usada.

        Este método existe para o caso em que o usuário diz "marca como
        feita", ou para propagar a conclusão de um bloco ligado
        (§5.7.1). Não existe caminho em que o modelo decida sozinho.
        """
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.COMPLETE,
                reason=reason,
                target_ref=before.id,
                before=before,
                sequence=self._take_sequence(),
            )
        )

    def propose_task_delete(self, before: Task, reason: str) -> DiffItem:
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.DELETE,
                reason=reason,
                target_ref=before.id,
                before=before,
                sequence=self._take_sequence(),
            )
        )

    def propose_task_reorder(
        self, list_id: str, ordered: list[Task], reason: str
    ) -> DiffItem:
        """Reordenação da lista, como UM item (§5.7.3).

        Um item e não N porque reordenar é uma decisão só: aprovar
        metade de uma reordenação não produz nada coerente. A ordem vai
        no `after` da primeira tarefa e os ids em `target_ref`.
        """
        if not ordered:
            raise ValueError("REORDER exige ao menos uma tarefa")
        return self._add(
            DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.REORDER,
                reason=reason,
                target_ref=list_id,
                before=ordered[0],
                after=ordered[0],
                reorder_ids=tuple(t.id or "" for t in ordered),
                sequence=self._take_sequence(),
            )
        )

    def propose_time_for_task(
        self,
        task: Task,
        block: Event,
        *,
        reason: str,
        task_reason: str | None = None,
    ) -> tuple[DiffItem, DiffItem]:
        """Reserva tempo para uma tarefa: DOIS itens ligados (§5.7.1).

        ```
        item_1: CREATE event  "Relatório do TCC"  qui 14:00-17:00
        item_2: UPDATE task   "Relatório do TCC"  due_at -> qui 14:00
        ```

        O vínculo importa por três razões concretas: aprovação conjunta
        (aceitar um e rejeitar o outro deixaria bloco sem tarefa, ou
        tarefa apontando para horário vazio), conclusão propagada
        (marcar o bloco conclui a tarefa, sem marcar duas vezes) e
        reversão conjunta no undo.

        Devolve `(item_evento, item_tarefa)` já ligados.
        """
        event_item = self.propose_create(block, reason)
        task_item = self.propose_task_update(
            task,
            replace(task, due_at=block.start, due_date=block.start.date()),
            task_reason
            or f"aponta para o bloco de {block.start:%d/%m às %H:%M}",
        )
        self.link(event_item.id, task_item.id)
        # Reler do plano para devolver os itens COM os links já
        # aplicados: `link` substitui os objetos na lista, então as
        # referências locais estariam desatualizadas.
        return (
            self._plan.item(event_item.id) or event_item,
            self._plan.item(task_item.id) or task_item,
        )

    def link(self, a: UUID, b: UUID) -> None:
        """Marca dois itens como unidade (§5.7.1)."""
        for index, item in enumerate(self._plan.items):
            if item.id == a:
                self._plan.items[index] = replace(item, links=(*item.links, b))
            elif item.id == b:
                self._plan.items[index] = replace(item, links=(*item.links, a))

    def set_summary(self, summary: str) -> None:
        self._plan.summary = summary

    def build(self) -> Plan:
        return self._plan

    @property
    def is_empty(self) -> bool:
        return not self._plan.items

    def _add(self, item: DiffItem) -> DiffItem:
        self._plan.items.append(item)
        return item

    def _take_sequence(self) -> int:
        value = self._next_sequence
        self._next_sequence += 1
        return value


# ---------------------------------------------------------------
# Validação (§5.7, passo 6 do laço)
# ---------------------------------------------------------------
def validate_plan(
    plan: Plan,
    existing_busy: list[BusyInterval],
    fixed_events: list[Event],
    *,
    now: datetime | None = None,
    max_hours_per_day: timedelta | None = None,
) -> Plan:
    """Detecta conflitos e anexa a cada item.

    Conflito não bloqueia por padrão: vira alerta destacado, e o usuário
    decide. Bloquear seria o sistema decidindo em nome dele — exceto
    quando a operação é literalmente impossível (evento no passado) ou
    perigosa (mexer em `FIXED`), que são `blocking`.
    """
    reference = now or datetime.now(UTC)
    # Intervalos de eventos intocaveis, por titulo, para a mensagem de
    # conflito dizer COM O QUE choca — "choca com a prova de Calculo" e
    # mais util que "choca com algo das 14h".
    fixed_spans = [
        (BusyInterval(start=e.start, end=e.end), e.title)
        for e in fixed_events
        if e.flexibility == Flexibility.FIXED
    ]

    # Carga por dia considerando o que o plano adiciona.
    added_per_day: dict[object, timedelta] = {}
    for item in plan.items:
        occupied = item.occupies
        if occupied is None:
            continue
        day = occupied.start.date()
        added_per_day[day] = added_per_day.get(day, timedelta()) + (
            occupied.end - occupied.start
        )

    validated: list[DiffItem] = []
    for item in plan.items:
        conflicts: list[Conflict] = []
        occupied = item.occupies

        if occupied is not None:
            if occupied.start < reference - PAST_GRACE:
                conflicts.append(
                    Conflict(
                        kind=ConflictKind.PAST,
                        message=(
                            f"{occupied.start:%d/%m às %H:%M} já passou."
                        ),
                        severity="blocking",
                    )
                )

            # Sobreposicao com evento INTOCAVEL e bloqueante: agendar
            # estudo em cima de uma prova nao e algo que o usuario
            # deva poder aprovar por descuido.
            freed = item.frees
            for span, title in fixed_spans:
                if freed is not None and (
                    span.start == freed.start and span.end == freed.end
                ):
                    continue
                if occupied.overlaps(span):
                    conflicts.append(
                        Conflict(
                            kind=ConflictKind.TOUCHES_FIXED_EVENT,
                            message=(
                                f"Choca com '{title}', que esta marcado "
                                f"como fixo ({span.start:%H:%M}-{span.end:%H:%M})."
                            ),
                            severity="blocking",
                        )
                    )
                    break

            # Sobreposicao com o resto: alerta, nao bloqueio. O usuario
            # pode ter motivo para sobrepor algo flexivel.
            for busy in existing_busy:
                if freed is not None and (
                    busy.start == freed.start and busy.end == freed.end
                ):
                    continue
                if any(busy.overlaps(span) for span, _ in fixed_spans):
                    continue  # ja reportado acima
                if occupied.overlaps(busy):
                    conflicts.append(
                        Conflict(
                            kind=ConflictKind.OVERLAP,
                            message=(
                                f"Choca com algo das "
                                f"{busy.start:%H:%M} as {busy.end:%H:%M}."
                            ),
                        )
                    )
                    break

            if max_hours_per_day is not None:
                day_total = added_per_day.get(occupied.start.date(), timedelta())
                if day_total > max_hours_per_day:
                    conflicts.append(
                        Conflict(
                            kind=ConflictKind.OVER_DAILY_LIMIT,
                            message=(
                                f"Passa do seu limite de "
                                f"{max_hours_per_day.total_seconds() / 3600:.0f}h "
                                f"por dia."
                            ),
                        )
                    )

        # Mexer em evento FIXED exige permissão explícita (§5.6).
        event_before = item.event_before
        if item.op in (Op.MOVE, Op.DELETE) and event_before is not None:
            if event_before.flexibility == Flexibility.FIXED:
                conflicts.append(
                    Conflict(
                        kind=ConflictKind.TOUCHES_FIXED_EVENT,
                        message=(
                            f"'{event_before.title}' está marcado como fixo. "
                            "Confirme se pode mesmo mexer."
                        ),
                        severity="blocking",
                    )
                )

        conflicts.extend(_task_conflicts(item, plan, reference))

        validated.append(replace(item, conflicts=tuple(conflicts)))

    plan.items = validated
    return plan


def _task_conflicts(
    item: DiffItem, plan: Plan, reference: datetime
) -> list[Conflict]:
    """Conflitos próprios de tarefa (§5.7.3).

    Nenhum deles é sobre ocupação de horário — tarefa não ocupa agenda
    (ver `DiffItem.occupies`). São sobre o prazo fazer sentido e sobre o
    provedor conseguir guardar o que foi pedido.
    """
    task = item.task_after or item.task_before
    if task is None:
        return []

    conflicts: list[Conflict] = []

    # Prazo no passado. Alerta, não bloqueio: repactuar uma tarefa
    # atrasada para "ontem" é erro, mas registrar algo cujo prazo já
    # passou é legítimo — é o caso das 4 atrasadas de §5.7.3.
    if item.op in (Op.CREATE, Op.MOVE, Op.UPDATE):
        deadline = task.due_at
        if deadline is not None and deadline < reference:
            conflicts.append(
                Conflict(
                    kind=ConflictKind.DUE_DATE_PASSED,
                    message=(
                        f"O prazo {deadline:%d/%m às %H:%M} já passou."
                    ),
                )
            )
        elif task.due_at is None and task.due_date is not None:
            if task.due_date < reference.date():
                conflicts.append(
                    Conflict(
                        kind=ConflictKind.DUE_DATE_PASSED,
                        message=(
                            f"O prazo {task.due_date:%d/%m} já passou."
                        ),
                    )
                )

    # O conflito que só existe porque as duas portas convivem: um bloco
    # reservado DEPOIS do prazo da tarefa a que está ligado. É um plano
    # que parece certo item por item e está errado como conjunto —
    # ninguém revisando dois cartões separados perceberia.
    #
    # ATENÇÃO a qual prazo comparar, porque errar aqui é fácil e o
    # sintoma é absurdo. Num par ligado, `task_after.due_at` é o INÍCIO
    # DO BLOCO: `propose_time_for_task` aponta a tarefa para o horário
    # reservado (§5.7.1). Comparar o fim do bloco com esse valor acusa
    # conflito em TODO par — o bloco sempre termina depois de começar.
    #
    # O prazo real é o de `task_before`, que é o que o usuário
    # combinou. Num CREATE não há `before`, e aí o `after` ainda é o
    # prazo de verdade.
    original = item.task_before or item.task_after
    for linked_id in item.links:
        other = plan.item(linked_id)
        if other is None:
            continue
        block = other.event_after
        if block is None or original is None:
            continue

        limit = original.due_at
        if limit is None and original.due_date is not None:
            # Prazo só com data vence no FIM do dia: "até sexta" é sexta
            # inteira. Comparar com 00:00 acusaria conflito em qualquer
            # bloco marcado no próprio dia do prazo — justamente o dia
            # mais provável de alguém escolher.
            limit = datetime.combine(
                original.due_date, time.max, tzinfo=block.start.tzinfo
            )
        if limit is not None and block.end > limit:
            conflicts.append(
                Conflict(
                    kind=ConflictKind.BLOCK_AFTER_DUE,
                    message=(
                        f"O bloco termina {block.end:%d/%m às %H:%M}, "
                        f"depois do prazo da tarefa "
                        f"({limit:%d/%m às %H:%M})."
                    ),
                    severity="blocking",
                )
            )

    # Campo que o provedor não guarda (§3.3.2). Alerta no cartão, para o
    # usuário saber ANTES de aprovar — e não descobrir depois, abrindo o
    # app do provedor.
    if task.dropped_fields:
        from app.ports.tasks import describe_degradation

        conflicts.append(
            Conflict(
                kind=ConflictKind.PROVIDER_WONT_STORE,
                message=describe_degradation(
                    "provedor da lista", task.dropped_fields
                ),
            )
        )

    return conflicts


# ---------------------------------------------------------------
# Ordenação de aplicação (§5.8)
# ---------------------------------------------------------------
_OP_ORDER = {
    Op.DELETE: 0,
    Op.MOVE: 1,
    Op.REORDER: 1,
    Op.UPDATE: 2,
    Op.COMPLETE: 2,
    Op.CREATE: 3,
}


def ordered_for_apply(items: list[DiffItem]) -> list[DiffItem]:
    """DELETE → MOVE → UPDATE → CREATE, e TASK antes de EVENT no par.

    A ordem não é estética: libera espaço antes de ocupar. Criar antes
    de mover geraria conflito artificial com o próprio plano.

    Dentro de um par ligado, a tarefa vem antes do evento — o evento
    aponta para ela (§5.8).
    """
    return sorted(
        items,
        key=lambda i: (
            _OP_ORDER.get(i.op, 9),
            0 if i.target == Target.TASK else 1,
            i.sequence,
        ),
    )
