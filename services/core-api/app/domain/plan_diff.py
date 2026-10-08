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
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from app.ports.calendar import BusyInterval, Event, Flexibility


class Target(StrEnum):
    """Qual porta recebe o item. `TASK` entra no M4 (§5.7)."""

    EVENT = "event"
    TASK = "task"


class Op(StrEnum):
    CREATE = "CREATE"
    MOVE = "MOVE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    # Só para tarefa, a partir do M4.
    COMPLETE = "COMPLETE"
    REORDER = "REORDER"


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
    before: Event | None = None
    after: Event | None = None
    confidence: float = 1.0
    conflicts: tuple[Conflict, ...] = ()
    # Ids de outros itens do mesmo plano que formam unidade (§5.7.1).
    links: tuple[UUID, ...] = ()
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

    @property
    def is_blocking(self) -> bool:
        return any(c.severity == "blocking" for c in self.conflicts)

    @property
    def occupies(self) -> BusyInterval | None:
        """Intervalo que este item passa a ocupar, se houver."""
        if self.after is None:
            return None
        return BusyInterval(start=self.after.start, end=self.after.end)

    @property
    def frees(self) -> BusyInterval | None:
        """Intervalo que este item libera, se houver."""
        if self.op in (Op.MOVE, Op.DELETE) and self.before is not None:
            return BusyInterval(start=self.before.start, end=self.before.end)
        return None


# Janela padrão de validade. Agenda muda; aprovar proposta de três dias
# atrás causaria estrago (§5.7).
DEFAULT_TTL = timedelta(hours=24)


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
            if occupied.start < reference:
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
        if item.op in (Op.MOVE, Op.DELETE) and item.before is not None:
            if item.before.flexibility == Flexibility.FIXED:
                conflicts.append(
                    Conflict(
                        kind=ConflictKind.TOUCHES_FIXED_EVENT,
                        message=(
                            f"'{item.before.title}' está marcado como fixo. "
                            "Confirme se pode mesmo mexer."
                        ),
                        severity="blocking",
                    )
                )

        validated.append(replace(item, conflicts=tuple(conflicts)))

    plan.items = validated
    return plan


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
