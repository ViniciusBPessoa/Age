"""Testes do Scheduler.

ARQUITETURA.md §5.3, §10. A invariante central é testada por
property-based: **nenhum slot devolvido intersecta intervalo ocupado.**
Se isso quebrar, o produto agenda duas coisas no mesmo horário — e a
confiança morre na primeira semana.

Aritmética de tempo é onde todo projeto de agenda sangra, então os casos
de borda (DST, virada de dia, lacuna exata) têm teste próprio.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.scheduler import (
    DailyWindow,
    SchedulingPolicy,
    find_free_slots,
    free_time,
    merge_busy,
    summarize_workload,
)
from app.ports.calendar import BusyInterval

SP = ZoneInfo("America/Sao_Paulo")


def dt(day: int, hour: int, minute: int = 0, *, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=SP)


def busy(day: int, h1: int, h2: int) -> BusyInterval:
    return BusyInterval(start=dt(day, h1), end=dt(day, h2))


def policy(**kw) -> SchedulingPolicy:
    base = {
        "timezone": "America/Sao_Paulo",
        "working_hours": {d: (DailyWindow(time(8), time(22)),) for d in range(7)},
        "buffer": timedelta(0),  # zero nos testes básicos, para isolar
        "max_hours_per_day": timedelta(hours=12),
    }
    return SchedulingPolicy(**{**base, **kw})


# ---------------------------------------------------------------
# merge_busy
# ---------------------------------------------------------------
def test_merge_empty() -> None:
    assert merge_busy([]) == []


def test_merge_overlapping() -> None:
    result = merge_busy([busy(8, 10, 12), busy(8, 11, 14)])
    assert len(result) == 1
    assert result[0].start == dt(8, 10)
    assert result[0].end == dt(8, 14)


def test_merge_adjacent() -> None:
    """Dois eventos encostados viram um: senão haveria lacuna de zero."""
    result = merge_busy([busy(8, 10, 12), busy(8, 12, 14)])
    assert len(result) == 1


def test_merge_keeps_separate() -> None:
    result = merge_busy([busy(8, 10, 11), busy(8, 14, 15)])
    assert len(result) == 2


def test_merge_contained_interval() -> None:
    """Um evento dentro de outro não estende o fim."""
    result = merge_busy([busy(8, 9, 18), busy(8, 12, 13)])
    assert len(result) == 1
    assert result[0].end == dt(8, 18)


def test_merge_is_order_independent() -> None:
    a = merge_busy([busy(8, 14, 15), busy(8, 10, 11)])
    b = merge_busy([busy(8, 10, 11), busy(8, 14, 15)])
    assert [(i.start, i.end) for i in a] == [(i.start, i.end) for i in b]


# ---------------------------------------------------------------
# find_free_slots — básico
# ---------------------------------------------------------------
def test_finds_slot_in_empty_day() -> None:
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(),
    )
    assert slots
    assert all(s.duration == timedelta(hours=1) for s in slots)


def test_no_slot_when_day_is_full() -> None:
    slots = find_free_slots(
        busy=[busy(8, 8, 22)],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(),
    )
    assert slots == []


def test_finds_slot_between_events() -> None:
    slots = find_free_slots(
        busy=[busy(8, 8, 12), busy(8, 14, 22)],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(),
    )
    assert slots
    for s in slots:
        assert s.start >= dt(8, 12)
        assert s.end <= dt(8, 14)


def test_exact_fit_gap_is_found() -> None:
    """Lacuna do tamanho exato da duração deve ser encontrada."""
    slots = find_free_slots(
        busy=[busy(8, 8, 12), busy(8, 13, 22)],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(),
    )
    assert len(slots) >= 1
    assert slots[0].start == dt(8, 12)


def test_gap_smaller_than_duration_is_rejected() -> None:
    slots = find_free_slots(
        busy=[busy(8, 8, 12), busy(8, 12, 22)],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(),
    )
    assert slots == []


def test_zero_duration_returns_nothing() -> None:
    """Entrada degenerada não deve gerar slot infinito."""
    assert (
        find_free_slots(
            busy=[],
            window_start=dt(8, 8),
            window_end=dt(8, 22),
            duration=timedelta(0),
            policy=policy(),
        )
        == []
    )


def test_inverted_window_returns_nothing() -> None:
    assert (
        find_free_slots(
            busy=[],
            window_start=dt(8, 18),
            window_end=dt(8, 8),
            duration=timedelta(hours=1),
            policy=policy(),
        )
        == []
    )


# ---------------------------------------------------------------
# Política
# ---------------------------------------------------------------
def test_respects_working_hours() -> None:
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 0),
        window_end=dt(8, 23, 59),
        duration=timedelta(hours=1),
        policy=policy(
            working_hours={d: (DailyWindow(time(9), time(17)),) for d in range(7)}
        ),
    )
    assert slots
    for s in slots:
        local = s.start.astimezone(SP)
        assert local.time() >= time(9)
        assert s.end.astimezone(SP).time() <= time(17)


def test_respects_blocked_windows() -> None:
    """Almoço bloqueado não aparece como livre."""
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(minutes=30),
        policy=policy(
            blocked_windows={d: (DailyWindow(time(12), time(13)),) for d in range(7)}
        ),
        max_results=100,
    )
    lunch = BusyInterval(start=dt(8, 12), end=dt(8, 13))
    assert all(not s.as_busy().overlaps(lunch) for s in slots)


def test_blocked_day_yields_nothing() -> None:
    """Dia sem janela de trabalho é dia inteiro indisponível."""
    # 2026-10-10 é um sábado.
    saturday = date(2026, 10, 10)
    assert saturday.weekday() == 5

    slots = find_free_slots(
        busy=[],
        window_start=datetime.combine(saturday, time(0), tzinfo=SP),
        window_end=datetime.combine(saturday, time(23, 59), tzinfo=SP),
        duration=timedelta(hours=1),
        policy=policy(
            working_hours={d: (DailyWindow(time(9), time(18)),) for d in range(5)}
        ),
    )
    assert slots == []


def test_max_hours_per_day_limits_what_the_age_schedules() -> None:
    """O limite controla o que o Age reserva, não o que já existe.

    Distinção que importa: se compromissos externos contassem no limite,
    quem tem agenda cheia nunca conseguiria encaixar nada — exatamente
    a pessoa que mais precisa do produto.
    """
    # 7h de compromissos externos NÃO impedem o Age de encaixar 1h.
    slots = find_free_slots(
        busy=[busy(8, 8, 15)],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(max_hours_per_day=timedelta(hours=6)),
    )
    assert slots, "compromisso externo não deve bloquear o encaixe"

    # Mas 6h já reservadas POR ESTA sessão de planejamento, sim.
    reserved = [BusyInterval(start=dt(8, 9), end=dt(8, 15))]
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(max_hours_per_day=timedelta(hours=6)),
        already_scheduled=reserved,
    )
    assert slots == [], "limite diário deve barrar após 6h planejadas"


def test_buffer_is_applied() -> None:
    """Buffer impede slot colado no evento anterior."""
    slots = find_free_slots(
        busy=[busy(8, 8, 12), busy(8, 16, 22)],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(buffer=timedelta(minutes=30)),
    )
    assert slots
    for s in slots:
        assert s.start >= dt(8, 12, 30)
        assert s.end <= dt(8, 15, 30)


def test_already_scheduled_blocks_slot() -> None:
    """Slots reservados na mesma sessão contam como ocupados (§5.5)."""
    reserved = [BusyInterval(start=dt(8, 14), end=dt(8, 15))]
    slots = find_free_slots(
        busy=[busy(8, 8, 12)],
        window_start=dt(8, 12),
        window_end=dt(8, 18),
        duration=timedelta(hours=1),
        policy=policy(),
        already_scheduled=reserved,
        max_results=100,
    )
    taken = reserved[0]
    assert all(not s.as_busy().overlaps(taken) for s in slots)


# ---------------------------------------------------------------
# Pontuação
# ---------------------------------------------------------------
def test_slot_after_deadline_is_discarded() -> None:
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(12, 22),
        duration=timedelta(hours=1),
        policy=policy(),
        deadline=dt(10, 8),
        max_results=200,
    )
    assert slots
    assert all(s.start < dt(10, 8) for s in slots)


def test_preferred_hours_score_higher() -> None:
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(preferred_hours=(DailyWindow(time(14), time(18)),)),
        max_results=100,
    )
    best = slots[0].start.astimezone(SP).time()
    assert time(14) <= best < time(18)


def test_last_day_before_deadline_is_penalized() -> None:
    """Véspera do prazo perde para dia anterior: margem importa (§5.4)."""
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(10, 22),
        duration=timedelta(hours=1),
        policy=policy(),
        deadline=dt(10, 23),
        max_results=200,
    )
    best_day = slots[0].start.astimezone(SP).date()
    assert best_day < date(2026, 10, 10)


def test_same_day_sessions_are_penalized() -> None:
    """Espaçamento para retenção: evita maratona (§5.4)."""
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(12, 22),
        duration=timedelta(hours=1),
        policy=policy(),
        subject_days=[date(2026, 10, 8)],
        max_results=200,
    )
    assert slots[0].start.astimezone(SP).date() != date(2026, 10, 8)


def test_reasons_are_generated() -> None:
    """A justificativa vem do cálculo, não do LLM (§5.7)."""
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 8),
        window_end=dt(8, 22),
        duration=timedelta(hours=1),
        policy=policy(preferred_hours=(DailyWindow(time(14), time(18)),)),
    )
    assert any(s.reasons for s in slots)


# ---------------------------------------------------------------
# Horário de verão e bordas de fuso
# ---------------------------------------------------------------
def test_dst_transition_does_not_crash() -> None:
    """O Brasil não tem DST desde 2019, mas outros fusos têm.

    Nova York vira o relógio em 08/03/2026 às 2h. Um produto de agenda
    tem de atravessar isso sem inventar nem perder hora.
    """
    ny = ZoneInfo("America/New_York")
    slots = find_free_slots(
        busy=[],
        window_start=datetime(2026, 3, 7, 8, tzinfo=ny),
        window_end=datetime(2026, 3, 9, 22, tzinfo=ny),
        duration=timedelta(hours=1),
        policy=SchedulingPolicy(
            timezone="America/New_York",
            working_hours={d: (DailyWindow(time(8), time(22)),) for d in range(7)},
            buffer=timedelta(0),
            max_hours_per_day=timedelta(hours=12),
        ),
        max_results=300,
    )
    assert slots
    # Nenhum slot com duração distorcida pela virada.
    assert all(s.duration == timedelta(hours=1) for s in slots)


def test_slots_never_cross_midnight() -> None:
    slots = find_free_slots(
        busy=[],
        window_start=dt(8, 20),
        window_end=dt(9, 10),
        duration=timedelta(hours=3),
        policy=policy(
            working_hours={d: (DailyWindow(time(6), time(23)),) for d in range(7)}
        ),
        max_results=100,
    )
    for s in slots:
        start_local = s.start.astimezone(SP)
        end_local = s.end.astimezone(SP)
        assert start_local.date() == end_local.date() or end_local.time() == time(0)


def test_slots_align_to_quarter_hour() -> None:
    """Slot começando 14:07 é válido e humanamente estranho."""
    slots = find_free_slots(
        busy=[BusyInterval(start=dt(8, 8), end=dt(8, 12, 7))],
        window_start=dt(8, 8),
        window_end=dt(8, 18),
        duration=timedelta(hours=1),
        policy=policy(),
        max_results=50,
    )
    assert slots
    assert all(s.start.minute % 15 == 0 for s in slots)


# ---------------------------------------------------------------
# A INVARIANTE CENTRAL — property-based
# ---------------------------------------------------------------
@st.composite
def busy_intervals(draw):
    """Gera intervalos ocupados aleatórios num dia."""
    count = draw(st.integers(min_value=0, max_value=6))
    out = []
    for _ in range(count):
        start_h = draw(st.integers(min_value=0, max_value=22))
        length = draw(st.integers(min_value=1, max_value=4))
        out.append(
            BusyInterval(
                start=dt(8, start_h),
                end=dt(8, min(start_h + length, 23)),
            )
        )
    return out


@settings(max_examples=250, deadline=None)
@given(
    intervals=busy_intervals(),
    duration_min=st.integers(min_value=15, max_value=180),
)
def test_no_slot_ever_overlaps_busy(intervals, duration_min) -> None:
    """A invariante que sustenta o produto inteiro.

    Nenhum slot devolvido pode intersectar intervalo ocupado. Se isso
    falhar, o assistente agenda estudo em cima de uma prova.
    """
    slots = find_free_slots(
        busy=intervals,
        window_start=dt(8, 0),
        window_end=dt(8, 23, 59),
        duration=timedelta(minutes=duration_min),
        policy=policy(
            working_hours={
                d: (DailyWindow(time(0, 0), time(23, 59)),) for d in range(7)
            }
        ),
        max_results=100,
    )
    merged = merge_busy(intervals)
    for slot in slots:
        for occupied in merged:
            assert not slot.as_busy().overlaps(occupied), (
                f"slot {slot.start}–{slot.end} colide com "
                f"{occupied.start}–{occupied.end}"
            )


@settings(max_examples=200, deadline=None)
@given(intervals=busy_intervals())
def test_merge_output_is_disjoint_and_sorted(intervals) -> None:
    """A saída da fusão é sempre disjunta e ordenada."""
    merged = merge_busy(intervals)
    for a, b in zip(merged, merged[1:], strict=False):
        assert a.end < b.start, "intervalos fundidos devem ser disjuntos"
        assert a.start <= b.start, "saída deve estar ordenada"


@settings(max_examples=150, deadline=None)
@given(
    intervals=busy_intervals(),
    duration_min=st.integers(min_value=15, max_value=120),
)
def test_every_slot_has_requested_duration(intervals, duration_min) -> None:
    duration = timedelta(minutes=duration_min)
    slots = find_free_slots(
        busy=intervals,
        window_start=dt(8, 0),
        window_end=dt(8, 23, 59),
        duration=duration,
        policy=policy(
            working_hours={
                d: (DailyWindow(time(0, 0), time(23, 59)),) for d in range(7)
            }
        ),
        max_results=50,
    )
    assert all(s.duration == duration for s in slots)


# ---------------------------------------------------------------
# summarize_workload
# ---------------------------------------------------------------
def test_workload_counts_hours_per_day() -> None:
    loads = summarize_workload(
        busy=[busy(8, 9, 12), busy(8, 14, 16), busy(9, 10, 11)],
        events_per_day={date(2026, 10, 8): 2, date(2026, 10, 9): 1},
        start=dt(8, 0),
        end=dt(9, 23),
        policy=policy(),
    )
    by_day = {load.day: load for load in loads}
    assert by_day[date(2026, 10, 8)].busy == timedelta(hours=5)
    assert by_day[date(2026, 10, 9)].busy == timedelta(hours=1)


def test_workload_flags_over_limit() -> None:
    loads = summarize_workload(
        busy=[busy(8, 8, 17)],  # 9h
        events_per_day={},
        start=dt(8, 0),
        end=dt(8, 23),
        policy=policy(max_hours_per_day=timedelta(hours=6)),
    )
    assert loads[0].over_limit is True


def test_workload_splits_event_crossing_midnight() -> None:
    """Evento que atravessa a meia-noite conta em cada dia."""
    loads = summarize_workload(
        busy=[BusyInterval(start=dt(8, 22), end=dt(9, 2))],
        events_per_day={},
        start=dt(8, 0),
        end=dt(9, 23),
        policy=policy(),
    )
    by_day = {load.day: load for load in loads}
    assert by_day[date(2026, 10, 8)].busy == timedelta(hours=2)
    assert by_day[date(2026, 10, 9)].busy == timedelta(hours=2)


# ---------------------------------------------------------------
# Tempo livre — o painel ao lado do chat
# ---------------------------------------------------------------
def _minutos(intervals: list[BusyInterval]) -> int:
    return int(sum((i.end - i.start).total_seconds() for i in intervals) // 60)


def test_tempo_livre_desconta_compromissos_e_fica_no_horario_util():
    # Das 14h às 23h, com reunião das 15h às 16h. Útil vai até 22h, então
    # sobra 14–15 e 16–22: 7h. Sabotagem: ignorar `working_hours` daria
    # 8h (contaria 22h–23h).
    livres = free_time([busy(8, 15, 16)], dt(8, 14), dt(8, 23), policy())
    assert [(i.start, i.end) for i in livres] == [
        (dt(8, 14), dt(8, 15)),
        (dt(8, 16), dt(8, 22)),
    ]
    assert _minutos(livres) == 7 * 60


def test_tempo_livre_respeita_faixa_bloqueada():
    # Almoço bloqueado das 12h às 13h todo dia. Sabotagem: usar só
    # `working_hours`, sem `_day_windows`, e o almoço contaria como livre.
    almoco = {d: (DailyWindow(time(12), time(13)),) for d in range(7)}
    livres = free_time([], dt(8, 11), dt(8, 14), policy(blocked_windows=almoco))
    assert _minutos(livres) == 2 * 60


def test_tempo_livre_funde_compromissos_sobrepostos():
    # Dois eventos se sobrepondo (10–12 e 11–13): o buraco é 10–13, não
    # duas subtrações que se anulam. Sabotagem: subtrair sem `merge_busy`.
    livres = free_time(
        [busy(8, 10, 12), busy(8, 11, 13)], dt(8, 9), dt(8, 14), policy()
    )
    assert _minutos(livres) == 2 * 60


def test_tempo_livre_janela_vazia_ou_invertida():
    assert free_time([], dt(8, 10), dt(8, 10), policy()) == []
    assert free_time([], dt(8, 12), dt(8, 10), policy()) == []


def test_tempo_livre_comeca_de_agora_e_nao_do_inicio_do_dia():
    # Às 21h30 só sobra meia hora até as 22h. Sabotagem: recortar pelo
    # dia inteiro em vez de por `start`.
    livres = free_time([], dt(8, 21, 30), dt(9, 0), policy())
    assert _minutos(livres) == 30
