"""Busca determinística de horários livres.

ARQUITETURA.md §5.3 e §5.4. Esta é a decisão que mais sustenta a
confiabilidade do produto: **o LLM não inventa horário livre.** Ele
chama `find_free_slots`, e o cálculo é código puro e testável.

A divisão de responsabilidade: o modelo decide *o quê* e *por quê*; o
código decide *quando cabe*. Um modelo fraco pode escrever uma
justificativa ruim, mas não consegue agendar duas coisas no mesmo
horário.

Função pura: nada de I/O, nada de banco. Recebe os intervalos ocupados
já unificados (o chamador junta calendário nativo e externos) e devolve
slots pontuados.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.ports.calendar import BusyInterval


# ---------------------------------------------------------------
# Política
# ---------------------------------------------------------------
@dataclass(frozen=True)
class DailyWindow:
    """Faixa utilizável de um dia da semana."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"janela inválida: {self.start} → {self.end}")


@dataclass(frozen=True)
class ScoringWeights:
    """Pesos da pontuação (§5.4), ajustáveis por usuário."""

    deadline_proximity: float = 1.0
    time_of_day: float = 1.0
    contiguity: float = 0.8
    daily_load: float = 1.2
    spacing: float = 1.0
    # Penaliza o último dia antes do prazo: zero margem para imprevisto.
    last_day_penalty: float = 2.0


@dataclass(frozen=True)
class SchedulingPolicy:
    """Regras do usuário (§6.1 `user_preferences`)."""

    timezone: str = "America/Sao_Paulo"
    # Por dia da semana (0 = segunda). Ausente = dia inteiro bloqueado.
    working_hours: dict[int, tuple[DailyWindow, ...]] = field(
        default_factory=lambda: {
            d: (DailyWindow(time(8, 0), time(22, 0)),) for d in range(7)
        }
    )
    # Faixas sempre indisponíveis (almoço, sono), por dia da semana.
    blocked_windows: dict[int, tuple[DailyWindow, ...]] = field(
        default_factory=dict
    )
    preferred_hours: tuple[DailyWindow, ...] = ()
    session_min: timedelta = timedelta(minutes=50)
    session_max: timedelta = timedelta(minutes=90)
    break_min: timedelta = timedelta(minutes=10)
    buffer: timedelta = timedelta(minutes=15)
    max_hours_per_day: timedelta = timedelta(hours=6)
    weights: ScoringWeights = field(default_factory=ScoringWeights)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


# ---------------------------------------------------------------
# Slot
# ---------------------------------------------------------------
@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    score: float = 0.0
    reasons: tuple[str, ...] = ()

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def as_busy(self) -> BusyInterval:
        return BusyInterval(start=self.start, end=self.end)


# ---------------------------------------------------------------
# 1. Normalização de intervalos ocupados
# ---------------------------------------------------------------
def merge_busy(intervals: list[BusyInterval]) -> list[BusyInterval]:
    """Ordena e funde intervalos que se sobrepõem ou encostam.

    Sem isso, dois eventos sobrepostos em calendários diferentes
    produziriam uma "lacuna" fantasma entre eles.
    """
    if not intervals:
        return []

    ordered = sorted(intervals, key=lambda i: (i.start, i.end))
    merged = [ordered[0]]

    for current in ordered[1:]:
        last = merged[-1]
        if current.start <= last.end:
            if current.end > last.end:
                merged[-1] = BusyInterval(start=last.start, end=current.end)
        else:
            merged.append(current)
    return merged


# ---------------------------------------------------------------
# 2. Janelas permitidas por dia
# ---------------------------------------------------------------
def _day_windows(
    day: date, policy: SchedulingPolicy
) -> list[BusyInterval]:
    """Faixas utilizáveis de um dia, já descontando bloqueios.

    Usa `BusyInterval` como par (start, end) — o nome é do contexto de
    ocupação, mas a estrutura serve para qualquer intervalo com fuso.
    """
    tz = policy.tz
    weekday = day.weekday()

    allowed: list[BusyInterval] = []
    for window in policy.working_hours.get(weekday, ()):
        # `datetime.combine` com tzinfo resolve DST corretamente: numa
        # virada de horário de verão, 23h pode não existir ou existir
        # duas vezes, e a tzinfo sabe disso.
        allowed.append(
            BusyInterval(
                start=datetime.combine(day, window.start, tzinfo=tz),
                end=datetime.combine(day, window.end, tzinfo=tz),
            )
        )

    blocked = [
        BusyInterval(
            start=datetime.combine(day, w.start, tzinfo=tz),
            end=datetime.combine(day, w.end, tzinfo=tz),
        )
        for w in policy.blocked_windows.get(weekday, ())
    ]

    return _subtract(allowed, blocked)


def _subtract(
    base: list[BusyInterval], holes: list[BusyInterval]
) -> list[BusyInterval]:
    """Remove `holes` de `base`, devolvendo os pedaços restantes."""
    if not holes:
        return base

    result = list(base)
    for hole in merge_busy(holes):
        next_round: list[BusyInterval] = []
        for span in result:
            if hole.end <= span.start or hole.start >= span.end:
                next_round.append(span)
                continue
            if hole.start > span.start:
                next_round.append(
                    BusyInterval(start=span.start, end=hole.start)
                )
            if hole.end < span.end:
                next_round.append(BusyInterval(start=hole.end, end=span.end))
        result = next_round
    return result


# ---------------------------------------------------------------
# 3. Busca
# ---------------------------------------------------------------
def find_free_slots(
    busy: list[BusyInterval],
    window_start: datetime,
    window_end: datetime,
    duration: timedelta,
    policy: SchedulingPolicy,
    *,
    deadline: datetime | None = None,
    max_results: int = 20,
    already_scheduled: list[BusyInterval] | None = None,
    subject_days: list[date] | None = None,
) -> list[Slot]:
    """Lacunas que cabem `duration`, pontuadas e ordenadas.

    `already_scheduled` são slots já reservados nesta mesma sessão de
    planejamento — o alocador reserva um por vez e recalcula, então eles
    contam como ocupados sem estar no calendário ainda (§5.5 passo 4).

    `subject_days` são dias que já têm sessão deste assunto; usado na
    pontuação de espaçamento para retenção.
    """
    if duration <= timedelta(0):
        return []
    if window_end <= window_start:
        return []

    occupied = merge_busy([*busy, *(already_scheduled or [])])
    tz = policy.tz

    # Carga por dia. Duas contas distintas, e confundi-las é bug:
    #
    #  - `planned_by_day` conta só o que ESTA sessão de planejamento já
    #    reservou. É o que o limite `max_hours_per_day` controla: o
    #    limite existe para o Age não entupir seu dia, não para desistir
    #    quando você já tem compromissos.
    #  - `total_by_day` conta tudo, inclusive compromissos externos. É o
    #    que a PONTUAÇÃO usa, para preferir dias mais vazios.
    #
    # Usar o total no limite faria quem tem agenda cheia nunca conseguir
    # encaixar nada — exatamente a pessoa que mais precisa de ajuda.
    planned_by_day: dict[date, timedelta] = {}
    for interval in already_scheduled or []:
        day = interval.start.astimezone(tz).date()
        planned_by_day[day] = planned_by_day.get(day, timedelta()) + (
            interval.end - interval.start
        )

    load_by_day: dict[date, timedelta] = {}
    for interval in occupied:
        day = interval.start.astimezone(tz).date()
        load_by_day[day] = load_by_day.get(day, timedelta()) + (
            interval.end - interval.start
        )

    slots: list[Slot] = []
    day = window_start.astimezone(tz).date()
    last_day = window_end.astimezone(tz).date()

    while day <= last_day:
        for span in _day_windows(day, policy):
            # Recorta pela janela pedida.
            span_start = max(span.start, window_start)
            span_end = min(span.end, window_end)
            if span_end <= span_start:
                continue

            for gap in _subtract(
                [BusyInterval(start=span_start, end=span_end)], occupied
            ):
                slots.extend(
                    _slots_in_gap(
                        gap,
                        duration,
                        policy,
                        day=day,
                        planned_load=planned_by_day.get(day, timedelta()),
                    )
                )
        day += timedelta(days=1)

    # Filtra o que esta DEPOIS do prazo antes de pontuar. Deixar para a
    # pontuacao resolver (com score muito negativo) nao basta: com
    # max_results alto, slots inuteis ocupariam o fim da lista e o
    # chamador poderia usa-los.
    if deadline is not None:
        slots = [s for s in slots if s.end <= deadline]

    scored = [
        replace(
            slot,
            score=_score(
                slot,
                policy=policy,
                deadline=deadline,
                day_load=load_by_day.get(slot.start.astimezone(tz).date(), timedelta()),
                subject_days=subject_days or [],
                gap_duration=slot.duration,
            ),
            reasons=_reasons(slot, policy, deadline),
        )
        for slot in slots
    ]
    scored.sort(key=lambda s: (-s.score, s.start))
    return _diversify(scored, max_results)


def _diversify(slots: list[Slot], limit: int) -> list[Slot]:
    """Evita devolver cinco slots que diferem em 15 minutos.

    A busca gera candidatos a cada 15 min, então os melhores tendem a
    ser vizinhos — tecnicamente corretos e inúteis para escolher. O
    usuário quer opções que sejam opções: dias diferentes, ou pelo menos
    turnos diferentes.

    A estratégia: no máximo um slot por (dia, bloco de 3h). Se ainda
    sobrar espaço no limite, completa com os melhores restantes — é
    melhor devolver vizinhos que devolver menos opções do que o pedido.
    """
    chosen: list[Slot] = []
    seen_buckets: set[tuple[object, int]] = set()

    for slot in slots:
        bucket = (slot.start.date(), slot.start.hour // 3)
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)
        chosen.append(slot)
        if len(chosen) >= limit:
            return chosen

    if len(chosen) < limit:
        already = {id(s) for s in chosen}
        for slot in slots:
            if id(slot) in already:
                continue
            chosen.append(slot)
            if len(chosen) >= limit:
                break

    return chosen


def _slots_in_gap(
    gap: BusyInterval,
    duration: timedelta,
    policy: SchedulingPolicy,
    *,
    day: date,
    planned_load: timedelta,
) -> list[Slot]:
    """Candidatos dentro de uma lacuna, respeitando buffer e limite diário."""
    needed = duration + policy.buffer * 2
    available = gap.end - gap.start

    if available < needed:
        # Sem buffer dos dois lados, tenta só com um — comum em lacuna
        # que começa ou termina no limite do dia.
        if available < duration + policy.buffer:
            return []

    # Limite de horas por dia (§5.4): conta só o que ESTA sessão de
    # planejamento já reservou, não os compromissos que já existiam.
    if planned_load + duration > policy.max_hours_per_day:
        return []

    usable_start = gap.start + policy.buffer
    usable_end = gap.end - policy.buffer
    if usable_end - usable_start < duration:
        usable_start = gap.start
        usable_end = gap.end
        if usable_end - usable_start < duration:
            return []

    # Candidatos a cada 15 min: granularidade fina o bastante para
    # parecer natural, grossa o bastante para não explodir o espaço.
    step = timedelta(minutes=15)
    out: list[Slot] = []
    cursor = _ceil_to(usable_start, step)

    while cursor + duration <= usable_end:
        out.append(Slot(start=cursor, end=cursor + duration))
        cursor += step
        if len(out) >= 40:  # proteção: lacuna muito longa
            break
    return out


def _ceil_to(moment: datetime, step: timedelta) -> datetime:
    """Arredonda para cima no múltiplo do passo.

    Slot começando 14:07 é tecnicamente válido e humanamente estranho.
    """
    seconds = int(step.total_seconds())
    epoch = int(moment.timestamp())
    remainder = epoch % seconds
    if remainder == 0:
        return moment
    return moment + timedelta(seconds=seconds - remainder)


# ---------------------------------------------------------------
# 4. Pontuação (§5.4)
# ---------------------------------------------------------------
def _score(
    slot: Slot,
    *,
    policy: SchedulingPolicy,
    deadline: datetime | None,
    day_load: timedelta,
    subject_days: list[date],
    gap_duration: timedelta,
) -> float:
    w = policy.weights
    tz = policy.tz
    local = slot.start.astimezone(tz)
    total = 0.0

    # Proximidade do prazo: mais perto vale mais, MAS o último dia é
    # penalizado — sem margem para imprevisto (§5.4).
    if deadline is not None:
        remaining = (deadline - slot.start).total_seconds()
        if remaining <= 0:
            return -1000.0  # depois do prazo: inútil
        days_left = remaining / 86400
        total += w.deadline_proximity * (1.0 / (1.0 + days_left / 7))
        if days_left < 1.0:
            total -= w.last_day_penalty

    # Horário preferido do usuário.
    if policy.preferred_hours:
        in_preferred = any(
            window.start <= local.time() < window.end
            for window in policy.preferred_hours
        )
        total += w.time_of_day * (1.0 if in_preferred else -0.5)

    # Bloco contíguo vale mais que pedaço picado.
    slack = gap_duration - slot.duration
    if slack <= timedelta(minutes=30):
        total += w.contiguity * 0.5

    # Dia já cheio é pior.
    load_ratio = (
        day_load / policy.max_hours_per_day
        if policy.max_hours_per_day
        else 0.0
    )
    total -= w.daily_load * load_ratio

    # Espaçamento para retenção: sessões distribuídas valem mais que
    # maratona. A penalidade cresce de forma superlinear porque a
    # diferença entre a 3ª e a 4ª sessão do mesmo assunto no mesmo dia é
    # pior que entre a 1ª e a 2ª — seis horas seguidas de álgebra não é
    # um plano de estudo, é desistência disfarçada.
    same_day = sum(1 for d in subject_days if d == local.date())
    if same_day:
        total -= w.spacing * (same_day**1.5)

    return total


def _reasons(
    slot: Slot, policy: SchedulingPolicy, deadline: datetime | None
) -> tuple[str, ...]:
    """Justificativa legível, que vira o `reason` do item de plano (§5.7).

    Gerar aqui, e não no LLM, garante que a explicação corresponda ao
    cálculo real.
    """
    tz = policy.tz
    local = slot.start.astimezone(tz)
    out: list[str] = []

    if policy.preferred_hours and any(
        w.start <= local.time() < w.end for w in policy.preferred_hours
    ):
        out.append("no seu horário preferido")

    if deadline is not None:
        days = (deadline - slot.start).total_seconds() / 86400
        if days < 1:
            out.append("véspera do prazo")
        elif days < 3:
            out.append("perto do prazo")

    return tuple(out)


# ---------------------------------------------------------------
# 4b. Tempo livre — "cabe mais alguma coisa hoje?"
# ---------------------------------------------------------------
def free_time(
    busy: list[BusyInterval],
    start: datetime,
    end: datetime,
    policy: SchedulingPolicy,
) -> list[BusyInterval]:
    """As lacunas livres entre `start` e `end`, em ordem.

    Livre quer dizer o mesmo que para `find_free_slots`: dentro do
    horário útil, fora das faixas bloqueadas (almoço, sono) e fora de
    qualquer compromisso. Sem duração mínima e sem `buffer`: a pergunta
    aqui é "quanto sobra", não "onde cabe uma sessão de 50 minutos" — e
    descontar folga faria o painel dizer "nada livre" com 40 minutos
    vagos na tela.

    O cálculo mora aqui, e não no front, por §9.5: a janela útil depende
    do fuso e das preferências da conta, e o browser não tem nenhum dos
    dois de forma confiável.
    """
    if end <= start:
        return []

    tz = policy.tz
    allowed: list[BusyInterval] = []
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while day <= last:
        for window in _day_windows(day, policy):
            lo = max(window.start, start)
            hi = min(window.end, end)
            if lo < hi:
                allowed.append(BusyInterval(start=lo, end=hi))
        day += timedelta(days=1)

    return _subtract(allowed, merge_busy(busy))


# ---------------------------------------------------------------
# 5. Carga por dia — alimenta o diagnóstico (§5.9.1)
# ---------------------------------------------------------------
@dataclass(frozen=True)
class DayLoad:
    day: date
    busy: timedelta
    event_count: int
    over_limit: bool


def summarize_workload(
    busy: list[BusyInterval],
    events_per_day: dict[date, int],
    start: datetime,
    end: datetime,
    policy: SchedulingPolicy,
) -> list[DayLoad]:
    """Horas ocupadas por dia, para "minha semana está pesada?"."""
    tz = policy.tz
    totals: dict[date, timedelta] = {}

    for interval in merge_busy(busy):
        # Um evento que atravessa a meia-noite conta em cada dia na
        # proporção correta.
        cursor = interval.start.astimezone(tz)
        remaining_end = interval.end.astimezone(tz)
        while cursor < remaining_end:
            day = cursor.date()
            midnight = datetime.combine(
                day + timedelta(days=1), time(0, 0), tzinfo=tz
            )
            chunk_end = min(midnight, remaining_end)
            totals[day] = totals.get(day, timedelta()) + (chunk_end - cursor)
            cursor = chunk_end

    out: list[DayLoad] = []
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while day <= last:
        busy_time = totals.get(day, timedelta())
        out.append(
            DayLoad(
                day=day,
                busy=busy_time,
                event_count=events_per_day.get(day, 0),
                over_limit=busy_time > policy.max_hours_per_day,
            )
        )
        day += timedelta(days=1)
    return out
