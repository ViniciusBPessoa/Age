"""Rotinas recorrentes — expansão, aderência e sobrecarga.

ARQUITETURA.md §5.9.4. Núcleo puro: recebe a regra e a agenda, devolve
ocorrências e números. Nada de I/O.

A decisão de §1.2 que molda este módulo: **não existe hábito
flutuante.** "Academia 3× por semana" é uma série de eventos reais que
ocupam tempo na agenda e entram no cálculo de conflito e de carga. É o
que o Age faz e um app de hábitos não faz — e é também por isso que
expandir a RRULE corretamente importa: errar aqui põe a academia de
alguém no dia errado.

Sobre `python-dateutil`: usar biblioteca para RFC 5545 não é preguiça.
A especificação tem casos de borda que parecem exóticos e não são —
`BYSETPOS`, `UNTIL` em UTC com `DTSTART` local, e principalmente a
virada do horário de verão, em que "toda terça às 19h" muda de offset
UTC no meio da série. Um expansor caseiro acerta os casos fáceis e
erra esses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from dateutil.rrule import rrulestr

from app.ports.calendar import BusyInterval, Event, Flexibility


class OccurrenceStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    SKIPPED = "skipped"
    MOVED = "moved"


@dataclass(frozen=True)
class Routine:
    """Uma série recorrente com hora marcada."""

    id: str | None
    title: str
    rrule: str
    duration: timedelta
    started_at: datetime
    flexibility: Flexibility = Flexibility.FLEXIBLE
    target_per_week: int | None = None
    category: str | None = None
    paused_at: datetime | None = None
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None:
            raise ValueError(
                f"Routine '{self.title}': started_at precisa de timezone. "
                "Uma série sem fuso erra o dia na virada do horário de "
                "verão."
            )
        if self.duration <= timedelta():
            raise ValueError(
                f"Routine '{self.title}': duração precisa ser positiva"
            )
        if not self.rrule.strip():
            raise ValueError(f"Routine '{self.title}': rrule vazia")

    @property
    def is_active(self) -> bool:
        return self.ended_at is None and self.paused_at is None

    @property
    def duration_min(self) -> int:
        return int(self.duration.total_seconds() // 60)


@dataclass(frozen=True)
class Occurrence:
    """Uma ocorrência concreta da série, com data e hora."""

    routine_id: str | None
    routine_title: str
    start: datetime
    end: datetime
    status: OccurrenceStatus = OccurrenceStatus.PLANNED
    actual_min: int | None = None
    moved_to: datetime | None = None
    event_ref_kind: str | None = None
    event_ref_id: str | None = None

    @property
    def interval(self) -> BusyInterval:
        return BusyInterval(start=self.start, end=self.end)

    @property
    def counts_as_done(self) -> bool:
        """Movida ainda conta como feita se foi realizada.

        "Movi a academia de terça para quinta e fui" é aderência, não
        falta. Contar como falta puniria justamente quem se adaptou em
        vez de desistir — e é o tipo de número errado que faz a pessoa
        parar de olhar o painel.
        """
        return self.status == OccurrenceStatus.DONE


# ---------------------------------------------------------------
# Expansão
# ---------------------------------------------------------------
def expand(
    routine: Routine,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int = 500,
) -> list[Occurrence]:
    """Ocorrências da série dentro da janela.

    O `limit` não é defensividade vaga: uma RRULE sem `UNTIL` nem
    `COUNT` é infinita, e `FREQ=MINUTELY` expandido num ano trava o
    processo. O corte é por segurança e o chamador trabalha em janelas.

    Respeita pausa e fim: uma série pausada não materializa ocorrências
    novas, mas as antigas continuam valendo para a aderência histórica
    — por isso o filtro é por data da ocorrência, não por "a série está
    ativa hoje".
    """
    if routine.ended_at is not None and routine.ended_at < window_start:
        return []

    effective_start = max(window_start, routine.started_at)
    effective_end = window_end
    if routine.ended_at is not None:
        effective_end = min(effective_end, routine.ended_at)
    if routine.paused_at is not None:
        # Pausa corta o futuro, não o passado.
        effective_end = min(effective_end, routine.paused_at)
    if effective_start >= effective_end:
        return []

    try:
        rule = rrulestr(routine.rrule, dtstart=routine.started_at)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Routine '{routine.title}': rrule inválida ({exc}). "
            "A série não é materializada em vez de gerar horário errado."
        ) from exc

    out: list[Occurrence] = []
    for moment in rule:
        if moment >= effective_end:
            break
        if moment < effective_start:
            continue
        out.append(
            Occurrence(
                routine_id=routine.id,
                routine_title=routine.title,
                start=moment,
                end=moment + routine.duration,
            )
        )
        if len(out) >= limit:
            break

    return out


def next_occurrence(
    routine: Routine, *, after: datetime, horizon_days: int = 90
) -> Occurrence | None:
    """A próxima ocorrência depois de um instante, se houver."""
    found = expand(
        routine,
        window_start=after,
        window_end=after + timedelta(days=horizon_days),
        limit=1,
    )
    return found[0] if found else None


# ---------------------------------------------------------------
# Conflito com o que já existe
# ---------------------------------------------------------------
@dataclass(frozen=True)
class OccurrenceConflict:
    occurrence: Occurrence
    with_title: str
    with_start: datetime
    with_end: datetime
    blocking: bool


def find_conflicts(
    occurrences: Iterable[Occurrence],
    events: Iterable[Event],
) -> list[OccurrenceConflict]:
    """Choques entre as ocorrências propostas e a agenda.

    Conflito com `FIXED` é bloqueante; com o resto é alerta. Mesma
    política de `validate_plan` (§5.7), e pelo mesmo motivo: o usuário
    pode ter razão para sobrepor algo flexível, mas sobrepor uma prova
    não é algo que ele deva aprovar por descuido.

    Devolve TODOS os choques, não só o primeiro: "só 2 conflitos no
    mês, nas duas semanas de prova" (§5.9.4) é a resposta útil, e ela
    exige contar.
    """
    event_list = list(events)
    out: list[OccurrenceConflict] = []

    for occurrence in occurrences:
        span = occurrence.interval
        for event in event_list:
            if event.start < span.end and span.start < event.end:
                out.append(
                    OccurrenceConflict(
                        occurrence=occurrence,
                        with_title=event.title,
                        with_start=event.start,
                        with_end=event.end,
                        blocking=event.flexibility == Flexibility.FIXED,
                    )
                )

    return out


def best_weekday_hour(
    candidates: Iterable[tuple[int, int]],
    events: Iterable[Event],
    *,
    window_start: datetime,
    window_end: datetime,
    duration: timedelta,
) -> tuple[int, int, int] | None:
    """Qual (dia da semana, hora) colide menos, dentro da janela.

    É o cálculo por trás de "seg/qua/sex às 19h é o que colide menos —
    só 2 conflitos no mês" (§5.9.4). O número vem daqui, não do modelo:
    mesma regra do `Scheduler`, o LLM não inventa horário.

    Devolve `(weekday, hour, n_conflitos)` do melhor candidato, ou
    `None` se a lista vier vazia.
    """
    event_list = list(events)
    best: tuple[int, int, int] | None = None

    for weekday, hour in candidates:
        clashes = 0
        moment = window_start.replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        while moment < window_end:
            if moment.weekday() == weekday and moment >= window_start:
                end = moment + duration
                clashes += sum(
                    1
                    for e in event_list
                    if e.start < end and moment < e.end
                )
            moment += timedelta(days=1)

        if best is None or clashes < best[2]:
            best = (weekday, hour, clashes)

    return best


# ---------------------------------------------------------------
# Aderência (§5.9.4) — sem streak, sem medalha
# ---------------------------------------------------------------
@dataclass(frozen=True)
class Adherence:
    """O que aconteceu com uma série num período.

    Deliberadamente **não** tem `streak`, `best_streak` nem `badge`.
    §5.9.4 é explícito: "não há streak, medalha nem gráfico de
    ofensiva". O motivo prático é que streak transforma uma falta em
    perda de progresso, o que faz a pessoa abandonar a série inteira
    depois de um dia ruim — o oposto do que o produto deveria causar.
    """

    routine_id: str | None
    routine_title: str
    planned: int
    done: int
    skipped: int
    moved: int
    total_minutes_done: int
    target_per_week: int | None
    weeks_in_period: float

    @property
    def completion(self) -> float:
        """Fração do que foi planejado e de fato aconteceu."""
        if self.planned == 0:
            return 0.0
        return self.done / self.planned

    @property
    def per_week(self) -> float:
        if self.weeks_in_period <= 0:
            return 0.0
        return self.done / self.weeks_in_period

    @property
    def meets_target(self) -> bool | None:
        """`None` quando não há meta declarada — não é falha, é ausência."""
        if self.target_per_week is None:
            return None
        return self.per_week >= self.target_per_week


def summarize_adherence(
    routine: Routine,
    occurrences: Iterable[Occurrence],
    *,
    period_start: datetime,
    period_end: datetime,
) -> Adherence:
    """Conta o que aconteceu. Sem interpretar, sem elogiar.

    A interpretação é do assistente, e §5.9.9 exige que ela só venha
    quando o dado sustenta: "parabéns, você está indo bem" com 2 de 6
    sessões feitas destrói a confiança no resto do que o Age diz.
    """
    in_period = [
        o
        for o in occurrences
        if period_start <= o.start < period_end
    ]

    done = sum(1 for o in in_period if o.status == OccurrenceStatus.DONE)
    skipped = sum(
        1 for o in in_period if o.status == OccurrenceStatus.SKIPPED
    )
    moved = sum(1 for o in in_period if o.status == OccurrenceStatus.MOVED)

    minutes = sum(
        o.actual_min
        if o.actual_min is not None
        else int((o.end - o.start).total_seconds() // 60)
        for o in in_period
        if o.status == OccurrenceStatus.DONE
    )

    weeks = max((period_end - period_start).days / 7.0, 0.0)

    return Adherence(
        routine_id=routine.id,
        routine_title=routine.title,
        planned=len(in_period),
        done=done,
        skipped=skipped,
        moved=moved,
        total_minutes_done=minutes,
        target_per_week=routine.target_per_week,
        weeks_in_period=weeks,
    )


# ---------------------------------------------------------------
# Padrão no que foi pulado (§5.9.9)
# ---------------------------------------------------------------
@dataclass(frozen=True)
class SkipPattern:
    """Um padrão encontrado no que a pessoa não fez.

    "As duas sessões puladas foram as de 7h da manhã" é o que torna o
    relatório útil (§5.9.9) — somar não basta. E a conclusão prática
    ("vale eu parar de agendar estudo cedo?") só pode ser oferecida se
    o padrão for real, por isso há um mínimo de amostras.
    """

    kind: str  # "hour" | "weekday"
    label: str
    skipped: int
    total: int

    @property
    def ratio(self) -> float:
        return self.skipped / self.total if self.total else 0.0


_MIN_SAMPLES_FOR_PATTERN = 3
_PATTERN_THRESHOLD = 0.6

_WEEKDAY_SHORT = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def format_moment(
    moment: datetime, *, timezone_name: str = "America/Sao_Paulo"
) -> str:
    """Data e hora como o usuário lê (§5.9.12: português do Brasil).

    Duas coisas que `strftime("%a")` erra e que importam:

    1. **O dia da semana sai em inglês**, porque o locale do container
       é C. "Tue 20/10" num produto em pt-BR é descuido visível.
    2. **A hora sai no fuso em que foi guardada**, que é UTC. Mostrar
       22:00 para um evento marcado às 19:00 é pior que o idioma
       errado: o usuário conclui que o sistema marcou na hora errada.

    Por isso a conversão de fuso é obrigatória aqui, e não opcional.
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001 — fuso ruim no perfil não derruba
        tz = ZoneInfo("America/Sao_Paulo")

    local = moment.astimezone(tz)
    return (
        f"{_WEEKDAY_SHORT[local.weekday()]} {local:%d/%m} às {local:%H:%M}"
    )


_WEEKDAY_NAMES = (
    "segunda",
    "terça",
    "quarta",
    "quinta",
    "sexta",
    "sábado",
    "domingo",
)


def find_skip_patterns(
    occurrences: Iterable[Occurrence],
    *,
    timezone_name: str = "America/Sao_Paulo",
) -> list[SkipPattern]:
    """Procura por que algo é pulado: horário ou dia da semana.

    Dois cortes deliberados:

    - **Mínimo de amostras.** Uma falta não é padrão. Dizer "você sempre
      pula de manhã" depois de uma falta é a interpretação apressada que
      §5.9.9 proíbe.
    - **Limiar alto.** 60% é exigente de propósito: um padrão fraco
      apresentado como descoberta vira ruído, e o usuário para de
      confiar nas observações.

    Agrupa por bloco de 3h, não por hora exata: "as de manhã" é a
    conclusão acionável, "as de 7h" só funciona se a pessoa sempre
    agendar exatamente 7h.
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001 — fuso inválido não derruba relatório
        tz = ZoneInfo("America/Sao_Paulo")

    items = [
        o
        for o in occurrences
        if o.status in (OccurrenceStatus.DONE, OccurrenceStatus.SKIPPED)
    ]
    if len(items) < _MIN_SAMPLES_FOR_PATTERN:
        return []

    by_bucket: dict[int, list[Occurrence]] = {}
    by_weekday: dict[int, list[Occurrence]] = {}
    for item in items:
        local = item.start.astimezone(tz)
        by_bucket.setdefault(local.hour // 3, []).append(item)
        by_weekday.setdefault(local.weekday(), []).append(item)

    out: list[SkipPattern] = []

    for bucket, group in sorted(by_bucket.items()):
        skipped = sum(
            1 for o in group if o.status == OccurrenceStatus.SKIPPED
        )
        if (
            len(group) >= _MIN_SAMPLES_FOR_PATTERN
            and skipped / len(group) >= _PATTERN_THRESHOLD
        ):
            out.append(
                SkipPattern(
                    kind="hour",
                    label=f"{bucket * 3:02d}h–{bucket * 3 + 3:02d}h",
                    skipped=skipped,
                    total=len(group),
                )
            )

    for weekday, group in sorted(by_weekday.items()):
        skipped = sum(
            1 for o in group if o.status == OccurrenceStatus.SKIPPED
        )
        if (
            len(group) >= _MIN_SAMPLES_FOR_PATTERN
            and skipped / len(group) >= _PATTERN_THRESHOLD
        ):
            out.append(
                SkipPattern(
                    kind="weekday",
                    label=_WEEKDAY_NAMES[weekday],
                    skipped=skipped,
                    total=len(group),
                )
            )

    # Mais forte primeiro: é o que o assistente deve mencionar, e só
    # um ou dois cabem numa resposta (§5.9.12).
    return sorted(out, key=lambda p: (-p.ratio, -p.total))


def describe_pattern(pattern: SkipPattern) -> str:
    """A frase que o assistente usa, com o dado dentro."""
    if pattern.kind == "hour":
        return (
            f"{pattern.skipped} de {pattern.total} no horário "
            f"{pattern.label} foram puladas"
        )
    return (
        f"{pattern.skipped} de {pattern.total} na {pattern.label} "
        "foram puladas"
    )


# ---------------------------------------------------------------
# Sobrecarga (§5.9.6)
# ---------------------------------------------------------------
@dataclass(frozen=True)
class OverloadOption:
    """Uma saída para um conflito, com o custo dito.

    §5.9.6: "dizer o que se perde, não só o que se ganha. 'Dá' sozinho
    é resposta incompleta." O campo `cost` não tem default por isso —
    uma opção sem custo declarado não deve existir.
    """

    label: str
    move_to: datetime
    cost: str
    recommended: bool = False

    def __post_init__(self) -> None:
        if not self.cost.strip():
            raise ValueError(
                "OverloadOption exige `cost`: uma opção sem o que se "
                "perde é resposta incompleta (§5.9.6)"
            )


def build_overload_options(
    displaced: Occurrence | Event,
    free_slots: list[BusyInterval],
    *,
    same_day_load: dict[date, int],
    max_options: int = 3,
    timezone_name: str = "America/Sao_Paulo",
) -> list[OverloadOption]:
    """Monta 2–3 saídas para algo que foi deslocado.

    §5.9.6 limita a 2–3 opções de propósito: "mais que isso é
    transferir o trabalho de decidir". E exige recomendar uma, porque
    "você decide" sem opinião é covardia de assistente.

    O critério da recomendação é concreto: prefere o dia que já tem
    menos coisa. Empilhar três blocos no mesmo dia é o custo que o
    usuário sente, e é o que a opção A de §5.9.6 descreve como
    "provavelmente cansativo".
    """
    original_day = displaced.start.date()
    duration = displaced.end - displaced.start

    viable = [
        slot
        for slot in free_slots
        if (slot.end - slot.start) >= duration
    ]
    if not viable:
        return []

    scored: list[tuple[int, BusyInterval]] = []
    for slot in viable:
        day = slot.start.date()
        load = same_day_load.get(day, 0)
        # Mesmo dia não é penalizado por ser o mesmo dia — é penalizado
        # pela carga que já tem. "Terça 19h" é ruim porque terça já tem
        # dois blocos, não porque é terça.
        scored.append((load, slot))

    scored.sort(key=lambda pair: (pair[0], pair[1].start))
    chosen = scored[: max(1, min(max_options, len(scored)))]
    best_load = chosen[0][0]

    # Qual opção recomendar, decidido ANTES de montar a lista.
    #
    # A primeira versão marcava `recommended=(load == best_load and not
    # same_day)`, e com empate de carga — o caso normal, porque dias
    # vazios têm todos carga zero — DUAS opções vinham recomendadas.
    # Duas recomendações é nenhuma: §5.9.6 pede "recomendar uma, com o
    # motivo", justamente porque "você decide" sem opinião transfere a
    # decisão de volta.
    #
    # O critério do desempate é a data mais próxima: entre dois dias
    # igualmente livres, atrasar menos é melhor.
    recommended_index = 0
    for index, (load, slot) in enumerate(chosen):
        if load == best_load and slot.start.date() != original_day:
            recommended_index = index
            break

    options: list[OverloadOption] = []
    for position, (load, slot) in enumerate(chosen):
        day = slot.start.date()
        same_day = day == original_day
        days_later = (day - original_day).days

        if same_day:
            cost = (
                f"mesmo dia, mas você fica com {load + 1} blocos "
                "seguidos — provavelmente cansativo"
            )
        elif days_later > 0:
            cost = (
                f"atrasa em {days_later} "
                f"{'dia' if days_later == 1 else 'dias'}"
            )
            if load == 0:
                cost = f"{day:%d/%m} está vazio, mas " + cost
        else:
            cost = f"antecipa em {abs(days_later)} dia(s)"

        options.append(
            OverloadOption(
                label=format_moment(slot.start, timezone_name=timezone_name),
                move_to=slot.start,
                cost=cost,
                recommended=(position == recommended_index),
            )
        )

    return options


def load_by_day(
    events: Iterable[Event], occurrences: Iterable[Occurrence] = ()
) -> dict[date, int]:
    """Quantos blocos cada dia já tem.

    Conta BLOCOS, não minutos: o cansaço de ter quatro coisas marcadas
    não é proporcional à duração delas, e é esse número que a
    recomendação de §5.9.6 usa.
    """
    out: dict[date, int] = {}
    for event in events:
        day = event.start.date()
        out[day] = out.get(day, 0) + 1
    for occurrence in occurrences:
        day = occurrence.start.date()
        out[day] = out.get(day, 0) + 1
    return out
