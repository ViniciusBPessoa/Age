"""Decomposição de assunto em sessões agendadas.

ARQUITETURA.md §5.5. O caso que originou o projeto: *"preciso estudar
Álgebra Linear, a prova é dia 20"*.

A divisão de trabalho é a mesma do resto do sistema, e é o que torna
isso confiável: **o LLM decompõe em tópicos** (precisa de conhecimento
da disciplina), **o código fatia e aloca** (precisa de aritmética
correta). Um modelo fraco pode estimar mal a duração de um tópico, mas
não consegue agendar duas sessões no mesmo horário nem ignorar um
pré-requisito.

Núcleo puro: recebe os tópicos já decompostos e os intervalos ocupados,
devolve as sessões alocadas. Nada de I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.domain.scheduler import SchedulingPolicy, Slot, find_free_slots
from app.ports.calendar import BusyInterval

log = logging.getLogger("core-api.planner")


class SessionKind(StrEnum):
    STUDY = "study"
    REVIEW = "review"


@dataclass(frozen=True)
class Topic:
    """Um tópico decomposto pelo modelo.

    `estimated` é apresentado ao usuário SEMPRE como estimativa (§5.5):
    o assistente não sabe a ementa da disciplina dele, e fingir que sabe
    seria o tipo de certeza falsa que §5.9.11 proíbe.
    """

    name: str
    estimated: timedelta
    order: int
    # Nomes de tópicos que precisam vir antes. Agendar "autovalores"
    # antes de "espaços vetoriais" é inútil mesmo com horário livre.
    prerequisites: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedSession:
    topic_name: str
    kind: SessionKind
    start: datetime
    end: datetime
    part: int = 1
    of_parts: int = 1
    reasons: tuple[str, ...] = ()

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def as_busy(self) -> BusyInterval:
        return BusyInterval(start=self.start, end=self.end)

    def title(self, subject: str) -> str:
        """Título do evento, legível na agenda.

        Inclui o assunto porque o usuário vê isso no Google Calendar
        junto de tudo mais: "Espaços vetoriais" sozinho não diz de que
        matéria é.

        O prefixo é condicional porque o nome do tópico vem do modelo,
        e ele às vezes já manda o assunto dentro. Prefixar sem olhar
        produziu "Funções em Python: Funções em Python: parâmetro x
        argumento" na agenda de um usuário, que viu o título repetido
        no cartão.
        """
        topico = self.topic_name.strip()
        alvo = subject.strip()
        # `casefold` e não `lower`: compara acento e caixa de forma
        # correta em português ("Funções" x "FUNÇÕES").
        if alvo and topico.casefold().startswith(alvo.casefold()):
            base = topico
        else:
            base = f"{alvo}: {topico}" if alvo else topico
        if self.kind == SessionKind.REVIEW:
            return f"{base} (revisão)"
        if self.of_parts > 1:
            return f"{base} ({self.part}/{self.of_parts})"
        return base


@dataclass
class StudyPlan:
    """Resultado da alocação.

    `unplaced` é a parte honesta: o que não couber é reportado, não
    cortado em silêncio (§5.5 passo 6).
    """

    subject: str
    sessions: list[PlannedSession] = field(default_factory=list)
    unplaced: list[tuple[str, timedelta]] = field(default_factory=list)
    total_estimated: timedelta = timedelta()
    total_scheduled: timedelta = timedelta()
    deadline: datetime | None = None

    @property
    def fits(self) -> bool:
        return not self.unplaced

    @property
    def missing(self) -> timedelta:
        return sum((d for _, d in self.unplaced), timedelta())

    @property
    def slack_days(self) -> int | None:
        """Dias entre a última sessão e o prazo.

        É a informação que permite confiar no plano: saber que há dois
        dias de folga muda como a pessoa encara um atraso (§5.5).
        """
        if self.deadline is None or not self.sessions:
            return None
        last = max(s.end for s in self.sessions)
        return max(0, (self.deadline - last).days)


# ---------------------------------------------------------------
# 1. Fatiamento
# ---------------------------------------------------------------
def slice_topics(
    topics: list[Topic], policy: SchedulingPolicy
) -> list[PlannedSession]:
    """Quebra tópicos longos em sessões do tamanho que a pessoa aguenta.

    Um tópico de 3h não vira uma sessão de 3h: vira duas de 90 min. O
    limite vem de `session_max`, que é preferência do usuário.

    Devolve sessões SEM horário (start/end sentinela) — a alocação é o
    passo seguinte. Separar os dois permite testar o fatiamento sozinho.
    """
    sessions: list[PlannedSession] = []
    epoch = datetime(1970, 1, 1, tzinfo=policy.tz)

    for topic in sorted(topics, key=lambda t: t.order):
        remaining = topic.estimated
        if remaining <= timedelta():
            continue

        # Quantas partes, arredondando para cima.
        parts = max(
            1,
            -(
                -int(remaining.total_seconds())
                // int(policy.session_max.total_seconds())
            ),
        )
        # Distribui o tempo igualmente, para não sobrar uma parte de
        # 10 minutos no fim.
        per_part = remaining / parts

        # Se a parte ficou abaixo do mínimo confortável, usa menos
        # partes maiores — exceto quando o total já é menor que o
        # mínimo, caso em que uma sessão curta é o certo.
        if per_part < policy.session_min and parts > 1:
            parts = max(
                1,
                int(remaining.total_seconds() // policy.session_min.total_seconds()),
            )
            per_part = remaining / parts

        for index in range(parts):
            sessions.append(
                PlannedSession(
                    topic_name=topic.name,
                    kind=SessionKind.STUDY,
                    start=epoch,
                    end=epoch + per_part,
                    part=index + 1,
                    of_parts=parts,
                )
            )

    return sessions


# ---------------------------------------------------------------
# 2. Ordenação por pré-requisito
# ---------------------------------------------------------------
def order_by_prerequisites(topics: list[Topic]) -> list[Topic]:
    """Ordenação topológica. Ciclo cai para a ordem declarada.

    Ciclo não é erro fatal: o modelo pode ter declarado dependência
    mútua por descuido, e derrubar o plano inteiro por isso seria
    desproporcional. Loga e segue.
    """
    by_name = {t.name: t for t in topics}
    visited: dict[str, int] = {}  # 0=visitando, 1=pronto
    ordered: list[Topic] = []
    had_cycle = False

    def visit(topic: Topic) -> None:
        nonlocal had_cycle
        state = visited.get(topic.name)
        if state == 1:
            return
        if state == 0:
            had_cycle = True
            return

        visited[topic.name] = 0
        for prereq_name in topic.prerequisites:
            prereq = by_name.get(prereq_name)
            if prereq is not None:
                visit(prereq)
        visited[topic.name] = 1
        ordered.append(topic)

    for topic in sorted(topics, key=lambda t: t.order):
        visit(topic)

    if had_cycle:
        log.info(
            "ciclo de pré-requisitos em %s; usando a ordem declarada",
            [t.name for t in topics],
        )
        return sorted(topics, key=lambda t: t.order)

    # Reescreve `order` para refletir a ordem topológica.
    return [replace(t, order=i) for i, t in enumerate(ordered)]


# ---------------------------------------------------------------
# 3. Alocação
# ---------------------------------------------------------------
def allocate_sessions(
    sessions: list[PlannedSession],
    busy: list[BusyInterval],
    window_start: datetime,
    window_end: datetime,
    policy: SchedulingPolicy,
    *,
    deadline: datetime | None = None,
    topic_order: dict[str, int] | None = None,
    prerequisites: dict[str, tuple[str, ...]] | None = None,
) -> tuple[list[PlannedSession], list[tuple[str, timedelta]]]:
    """Aloca uma sessão por vez, recalculando a cada passo.

    O recálculo não é desperdício: reservar um slot muda a pontuação dos
    demais (espaçamento, carga do dia), e alocar tudo de uma vez
    produziria duas sessões do mesmo assunto no mesmo dia (§5.5 passo 4).

    Duas restrições convivem aqui, e confundi-las foi um bug real:

    - **Ordem de pré-requisito** é por TÓPICO. "Autovalores" depois de
      "espaços vetoriais". Rastrear isso com um único `earliest` global
      serializava todas as sessões e travava o plano: a primeira ia para
      o dia mais próximo do prazo (a pontuação prefere isso) e nada mais
      caberia depois.
    - **Partes de um mesmo tópico** seguem em ordem entre si: a parte 2
      não começa antes da parte 1 terminar.

    `topic_order` dá a posição de cada tópico na ordenação topológica;
    sem ele, a ordem da lista é usada.
    """
    placed: list[PlannedSession] = []
    unplaced: list[tuple[str, timedelta]] = []
    reserved: list[BusyInterval] = []
    subject_days: list[date] = []

    # Fim da última parte de cada tópico, para ordenar as partes entre si.
    topic_end: dict[str, datetime] = {}
    # Fim da sessão anterior: o plano é sequencial.
    previous_end: datetime | None = None

    if topic_order is None:
        topic_order = {}
        for session in sessions:
            topic_order.setdefault(session.topic_name, len(topic_order))
    if prerequisites is None:
        prerequisites = {}

    for session in sessions:
        # Piso: a parte anterior DESTE tópico, e os pré-requisitos dele.
        floor = window_start
        own_end = topic_end.get(session.topic_name)
        if own_end is not None:
            floor = max(floor, own_end)

        # Um plano de estudo é SEQUENCIAL: cada sessão vem depois da
        # anterior. O modelo lista os tópicos na ordem em que devem ser
        # estudados, e "revisão e exercícios" no fim da lista significa
        # no fim do plano — mesmo sem pré-requisito declarado.
        #
        # Nota sobre a história deste trecho, porque ela é instrutiva:
        # eu removi esta serialização antes, achando que ela travava o
        # plano. Travava mesmo — mas a causa era outra: a PONTUAÇÃO
        # (§5.4) favorece horários perto do prazo, então a primeira
        # sessão ia para o último dia e nada mais caberia depois.
        # Corrigido isso com `_earliest_good`, serializar voltou a ser
        # o comportamento certo.
        if previous_end is not None:
            floor = max(floor, previous_end)

        # Pré-requisito declarado reforça: se o tópico depende de outro
        # que ainda não terminou, espera.
        for prereq in prerequisites.get(session.topic_name, ()):
            prereq_end = topic_end.get(prereq)
            if prereq_end is not None:
                floor = max(floor, prereq_end)

        # Reparte a janela a partir do PISO desta sessão, não do início
        # absoluto. Usar `window_start` fazia as primeiras sessões
        # ignorarem os primeiros dias (o trecho 0 era o começo da
        # janela, mas o piso já estava adiante) e empilhava o resto no
        # fim: 6h seguidas na véspera, com os primeiros dias vazios.
        # O espaçamento vem da penalidade superlinear de §5.4 e do
        # piso de pré-requisito. Um teto rígido de sessões por dia foi
        # tentado aqui e não ajudou: os dias só ficavam "cheios" no fim
        # da alocação, quando já não havia alternativa.
        preferred_start = floor

        candidates = _earliest_good(
            find_free_slots(
                busy=busy,
                window_start=preferred_start,
                window_end=window_end,
                duration=session.duration,
                policy=policy,
                deadline=deadline,
                max_results=40,
                already_scheduled=reserved,
                subject_days=subject_days,
            )
        )
        slots = candidates

        if not slots and preferred_start > floor:
            # O trecho não tinha espaço: tenta desde o piso real.
            slots = _earliest_good(
                find_free_slots(
                    busy=busy,
                    window_start=floor,
                    window_end=window_end,
                    duration=session.duration,
                    policy=policy,
                    deadline=deadline,
                    max_results=25,
                    already_scheduled=reserved,
                    subject_days=subject_days,
                )
            )

        if not slots:
            shortened = _try_shorter(
                session, busy, floor, window_end, policy, deadline,
                reserved, subject_days,
            )
            if shortened is None:
                unplaced.append((session.topic_name, session.duration))
                continue
            slot, session = shortened
        else:
            slot = slots[0]

        final = replace(
            session, start=slot.start, end=slot.end, reasons=slot.reasons
        )
        placed.append(final)
        reserved.append(final.as_busy())
        subject_days.append(slot.start.astimezone(policy.tz).date())
        previous_end = final.end
        topic_end[session.topic_name] = max(
            topic_end.get(session.topic_name, final.end), final.end
        )

    return placed, unplaced


def _earliest_good(slots: list[Slot], tolerance: float = 0.35) -> list[Slot]:
    """Prefere o slot MAIS CEDO disponível, não o de maior nota.

    A pontuação de §5.4 favorece horários perto do prazo — certo para
    "reserve um tempo pra resolver o IR", errado para estudo: as sessões
    precisam começar cedo e espaçadas, deixando folga antes da prova.

    Uma tolerância fixa sobre a nota não resolve, e vale registrar por
    quê: a diferença entre o melhor slot (véspera) e um slot de duas
    semanas antes passa de qualquer tolerância razoável, então os
    horários cedo nem entravam na lista. O plano começava quatro dias
    antes da prova com 13 dias livres disponíveis.

    Então a ordenação é **por data primeiro**. A nota decide o desempate
    dentro do mesmo dia, que é onde ela ajuda de verdade — horário
    preferido, bloco contíguo, dia menos cheio.
    """
    if not slots:
        return []

    by_day: dict[object, list[Slot]] = {}
    for slot in slots:
        by_day.setdefault(slot.start.date(), []).append(slot)

    ordered: list[Slot] = []
    for day in sorted(by_day):
        # Dentro do dia, a melhor nota primeiro.
        ordered.extend(sorted(by_day[day], key=lambda s: -s.score))
    return ordered


def _try_shorter(
    session: PlannedSession,
    busy: list[BusyInterval],
    window_start: datetime,
    window_end: datetime,
    policy: SchedulingPolicy,
    deadline: datetime | None,
    reserved: list[BusyInterval],
    subject_days: list[date],
) -> tuple[Slot, PlannedSession] | None:
    """Reduz a sessão até `session_min` procurando espaço.

    Encurtar é melhor que não agendar: uma sessão de 50 min que acontece
    vale mais que uma de 90 que não cabe.
    """
    duration = session.duration
    while duration > policy.session_min:
        duration = max(policy.session_min, duration - timedelta(minutes=15))
        slots = _earliest_good(
            find_free_slots(
                busy=busy,
                window_start=window_start,
                window_end=window_end,
                duration=duration,
                policy=policy,
                deadline=deadline,
                max_results=15,
                already_scheduled=reserved,
                subject_days=subject_days,
            )
        )
        if slots:
            # Recria com a duração nova; o start real vem do slot.
            return slots[0], replace(
                session, start=slots[0].start, end=slots[0].start + duration
            )
    return None


# ---------------------------------------------------------------
# 4. Revisão espaçada
# ---------------------------------------------------------------
# D+1, D+3, D+7 é o espaçamento clássico de retenção. Revisões são
# curtas de propósito: relembrar custa menos que aprender.
_REVIEW_OFFSETS = (1, 3, 7)
_REVIEW_DURATION = timedelta(minutes=25)


def add_spaced_reviews(
    sessions: list[PlannedSession],
    busy: list[BusyInterval],
    window_end: datetime,
    policy: SchedulingPolicy,
    *,
    deadline: datetime | None = None,
    max_reviews_per_topic: int = 2,
) -> list[PlannedSession]:
    """Acrescenta revisões curtas após cada tópico.

    Revisão que não cabe é simplesmente omitida, sem reportar como
    falha: ela é bônus, e dizer "não consegui agendar sua revisão"
    criaria ansiedade por algo opcional.
    """
    reviews: list[PlannedSession] = []
    reserved = [s.as_busy() for s in sessions]

    # Última sessão de cada tópico: a revisão vem depois dela.
    last_by_topic: dict[str, PlannedSession] = {}
    for session in sessions:
        if session.kind != SessionKind.STUDY:
            continue
        current = last_by_topic.get(session.topic_name)
        if current is None or session.end > current.end:
            last_by_topic[session.topic_name] = session

    for topic_name, last in last_by_topic.items():
        added = 0
        for offset in _REVIEW_OFFSETS:
            if added >= max_reviews_per_topic:
                break

            target = last.end + timedelta(days=offset)
            if target >= window_end:
                break
            if deadline is not None and target >= deadline:
                break

            slots = find_free_slots(
                busy=busy,
                window_start=target.replace(hour=0, minute=0),
                window_end=min(target + timedelta(days=1), window_end),
                duration=_REVIEW_DURATION,
                policy=policy,
                deadline=deadline,
                max_results=1,
                already_scheduled=reserved,
            )
            if not slots:
                continue

            slot = slots[0]
            review = PlannedSession(
                topic_name=topic_name,
                kind=SessionKind.REVIEW,
                start=slot.start,
                end=slot.end,
                reasons=(f"revisão D+{offset} para fixar",),
            )
            reviews.append(review)
            reserved.append(review.as_busy())
            added += 1

    return reviews


# ---------------------------------------------------------------
# 5. Orquestração
# ---------------------------------------------------------------
def build_study_plan(
    subject: str,
    topics: list[Topic],
    busy: list[BusyInterval],
    window_start: datetime,
    window_end: datetime,
    policy: SchedulingPolicy,
    *,
    deadline: datetime | None = None,
    include_reviews: bool = True,
) -> StudyPlan:
    """Monta o plano completo: ordena, fatia, aloca, revisa."""
    if not topics:
        return StudyPlan(subject=subject, deadline=deadline)

    ordered = order_by_prerequisites(topics)
    sessions = slice_topics(ordered, policy)

    # Nunca agenda no dia da prova sem perguntar (§5.5): a janela
    # termina na véspera.
    effective_end = window_end
    if deadline is not None:
        day_before = deadline.replace(hour=0, minute=0, second=0, microsecond=0)
        effective_end = min(window_end, day_before)
        if effective_end <= window_start:
            # Prazo é hoje ou amanhã: usa até o próprio prazo, porque
            # recusar tudo seria pior que agendar apertado.
            effective_end = min(window_end, deadline)

    placed, unplaced = allocate_sessions(
        sessions,
        busy,
        window_start,
        effective_end,
        policy,
        deadline=deadline,
        topic_order={t.name: t.order for t in ordered},
        prerequisites={t.name: t.prerequisites for t in ordered},
    )

    if include_reviews and placed:
        placed.extend(
            add_spaced_reviews(
                placed, busy, effective_end, policy, deadline=deadline
            )
        )

    placed.sort(key=lambda s: s.start)

    return StudyPlan(
        subject=subject,
        sessions=placed,
        unplaced=unplaced,
        total_estimated=sum((t.estimated for t in topics), timedelta()),
        total_scheduled=sum(
            (s.duration for s in placed if s.kind == SessionKind.STUDY),
            timedelta(),
        ),
        deadline=deadline,
    )


def describe_shortfall(plan: StudyPlan) -> str:
    """Mensagem honesta quando não cabe tudo (§5.5 passo 6).

    O assistente nunca entrega um plano que parece completo mas não é.
    Esta função produz o texto que explica o que sobrou e oferece as
    saídas reais — não "não consegui".
    """
    if plan.fits:
        return ""

    hours = plan.missing.total_seconds() / 3600
    names = ", ".join(name for name, _ in plan.unplaced)
    scheduled = plan.total_scheduled.total_seconds() / 3600
    total = plan.total_estimated.total_seconds() / 3600

    return (
        f"Não cabe tudo. Consegui {scheduled:.1f}h das {total:.1f}h "
        f"estimadas; faltaram {hours:.1f}h ({names}).\n\n"
        "Opções:\n"
        "- estender o prazo, se der\n"
        "- reduzir a profundidade de algum tópico\n"
        "- abrir um horário que você bloqueou"
    )
