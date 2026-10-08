"""Testes de rotina recorrente, aderência e sobrecarga.

ARQUITETURA.md §5.9.4, §5.9.6 e §5.9.9. Três coisas difíceis de acertar
e fáceis de errar sem perceber:

1. **Expansão de RRULE na virada do horário de verão.** "Toda terça às
   19h" muda de offset UTC no meio da série, e o erro aparece como a
   academia de alguém marcada uma hora errada — plausível o bastante
   para passar.
2. **Aderência com ocorrência movida.** Contar "movi de terça pra
   quinta e fui" como falta puniria quem se adaptou em vez de desistir.
3. **Padrão no que foi pulado.** Uma falta não é padrão; dizer que é
   destrói a confiança nas observações.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.routines import (
    Occurrence,
    OccurrenceStatus,
    Routine,
    best_weekday_hour,
    build_overload_options,
    describe_pattern,
    expand,
    find_conflicts,
    find_skip_patterns,
    load_by_day,
    next_occurrence,
    summarize_adherence,
)
from app.ports.calendar import BusyInterval, Event, Flexibility

SP = ZoneInfo("America/Sao_Paulo")


def academia(
    *,
    rrule: str = "FREQ=WEEKLY;BYDAY=MO,WE,FR",
    start: datetime | None = None,
    duration_min: int = 60,
    **extra,
) -> Routine:
    return Routine(
        id="r1",
        title="Academia",
        rrule=rrule,
        duration=timedelta(minutes=duration_min),
        started_at=start or datetime(2026, 10, 12, 19, tzinfo=SP),
        target_per_week=3,
        **extra,
    )


def an_event(
    *,
    title: str = "Prova de Cálculo",
    day: int = 14,
    hour: int = 19,
    hours: int = 2,
    flexibility: Flexibility = Flexibility.FIXED,
) -> Event:
    start = datetime(2026, 10, day, hour, tzinfo=SP)
    return Event(
        id=f"e-{day}-{hour}",
        calendar_id="primary",
        title=title,
        start=start,
        end=start + timedelta(hours=hours),
        flexibility=flexibility,
    )


def done(start: datetime, *, actual: int | None = None) -> Occurrence:
    return Occurrence(
        routine_id="r1",
        routine_title="Academia",
        start=start,
        end=start + timedelta(hours=1),
        status=OccurrenceStatus.DONE,
        actual_min=actual,
    )


def skipped(start: datetime) -> Occurrence:
    return Occurrence(
        routine_id="r1",
        routine_title="Academia",
        start=start,
        end=start + timedelta(hours=1),
        status=OccurrenceStatus.SKIPPED,
    )


# ---------------------------------------------------------------
# Expansão
# ---------------------------------------------------------------
def test_weekly_rrule_hits_the_requested_weekdays() -> None:
    routine = academia()
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 10, 26, tzinfo=SP),
    )
    weekdays = {o.start.weekday() for o in found}
    assert weekdays == {0, 2, 4}  # seg, qua, sex
    assert all(o.start.hour == 19 for o in found)


def test_duration_is_applied_to_every_occurrence() -> None:
    routine = academia(duration_min=90)
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 10, 20, tzinfo=SP),
    )
    assert found
    assert all(o.end - o.start == timedelta(minutes=90) for o in found)


def test_occurrences_keep_wall_clock_time_across_dst() -> None:
    """A virada do horário de verão não pode mover a rotina.

    O Brasil não tem mais DST, mas o produto não assume isso: o fuso é
    do usuário e `America/Sao_Paulo` já teve DST. O que precisa valer é
    que "19h" seja 19h no relógio da pessoa em toda ocorrência — não um
    offset UTC fixo, que faria metade da série cair às 18h ou 20h.

    O teste roda sobre um fuso que AINDA tem DST, para ser real.
    """
    ny = ZoneInfo("America/New_York")
    routine = Routine(
        id="r2",
        title="Corrida",
        rrule="FREQ=WEEKLY;BYDAY=SU",
        duration=timedelta(minutes=45),
        # Antes da virada de novembro de 2026 nos EUA.
        started_at=datetime(2026, 10, 25, 7, tzinfo=ny),
    )
    found = expand(
        routine,
        window_start=datetime(2026, 10, 20, tzinfo=ny),
        window_end=datetime(2026, 11, 30, tzinfo=ny),
    )
    assert len(found) >= 4
    local_hours = {o.start.astimezone(ny).hour for o in found}
    assert local_hours == {7}, (
        f"a rotina mudou de hora na virada do DST: {sorted(local_hours)}"
    )


def test_paused_routine_stops_materializing_the_future() -> None:
    """Pausa corta o futuro, não o passado.

    A aderência histórica continua valendo — é o que dá valor ao
    painel, e apagá-la seria o motivo de ninguém pausar nada.
    """
    routine = academia(paused_at=datetime(2026, 10, 19, tzinfo=SP))
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 10, 31, tzinfo=SP),
    )
    assert found, "ocorrências antes da pausa deveriam existir"
    assert all(o.start < datetime(2026, 10, 19, tzinfo=SP) for o in found)


def test_ended_routine_before_window_yields_nothing() -> None:
    routine = academia(ended_at=datetime(2026, 10, 1, tzinfo=SP))
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 10, 31, tzinfo=SP),
    )
    assert found == []


def test_infinite_rrule_is_capped() -> None:
    """RRULE sem UNTIL nem COUNT é infinita.

    Sem o corte, `FREQ=HOURLY` numa janela de um ano travaria o
    processo — e a janela vem de parâmetro de API.
    """
    routine = academia(rrule="FREQ=HOURLY")
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2027, 10, 12, tzinfo=SP),
        limit=50,
    )
    assert len(found) == 50


def test_invalid_rrule_raises_instead_of_guessing() -> None:
    """Regra inválida não materializa nada em vez de inventar horário."""
    routine = academia(rrule="ISSO NAO E UMA RRULE")
    with pytest.raises(ValueError, match="rrule inválida"):
        expand(
            routine,
            window_start=datetime(2026, 10, 12, tzinfo=SP),
            window_end=datetime(2026, 10, 20, tzinfo=SP),
        )


def test_naive_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Routine(
            id=None,
            title="x",
            rrule="FREQ=DAILY",
            duration=timedelta(hours=1),
            started_at=datetime(2026, 10, 12, 19),
        )


def test_next_occurrence_finds_the_following_one() -> None:
    routine = academia()
    nxt = next_occurrence(
        routine, after=datetime(2026, 10, 13, 12, tzinfo=SP)
    )
    assert nxt is not None
    assert nxt.start == datetime(2026, 10, 14, 19, tzinfo=SP)  # quarta


# ---------------------------------------------------------------
# Conflito
# ---------------------------------------------------------------
def test_conflict_with_fixed_event_is_blocking() -> None:
    routine = academia()
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 10, 20, tzinfo=SP),
    )
    clashes = find_conflicts(found, [an_event(day=14, hour=19)])
    assert len(clashes) == 1
    assert clashes[0].blocking
    assert clashes[0].with_title == "Prova de Cálculo"


def test_conflict_with_flexible_event_is_only_a_warning() -> None:
    routine = academia()
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 10, 20, tzinfo=SP),
    )
    clashes = find_conflicts(
        found,
        [an_event(day=14, hour=19, flexibility=Flexibility.FLEXIBLE)],
    )
    assert len(clashes) == 1
    assert not clashes[0].blocking


def test_all_conflicts_are_returned_not_just_the_first() -> None:
    """"Só 2 conflitos no mês" (§5.9.4) exige contar, não detectar."""
    routine = academia()
    found = expand(
        routine,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 11, 12, tzinfo=SP),
    )
    clashes = find_conflicts(
        found, [an_event(day=14, hour=19), an_event(day=21, hour=19)]
    )
    assert len(clashes) == 2


def test_best_weekday_hour_picks_the_least_crowded() -> None:
    """O número de §5.9.4 vem do cálculo, não do modelo."""
    busy_evenings = [
        an_event(day=d, hour=19, flexibility=Flexibility.FIXED)
        for d in (13, 20, 27)  # três terças ocupadas
    ]
    best = best_weekday_hour(
        [(1, 19), (0, 19)],  # terça 19h vs. segunda 19h
        busy_evenings,
        window_start=datetime(2026, 10, 12, tzinfo=SP),
        window_end=datetime(2026, 11, 9, tzinfo=SP),
        duration=timedelta(hours=1),
    )
    assert best is not None
    weekday, hour, clashes = best
    assert weekday == 0, "segunda deveria ganhar — terça está ocupada"
    assert clashes == 0


# ---------------------------------------------------------------
# Aderência (§5.9.4)
# ---------------------------------------------------------------
def test_adherence_counts_what_happened() -> None:
    routine = academia()
    occurrences = [
        done(datetime(2026, 10, 12, 19, tzinfo=SP)),
        done(datetime(2026, 10, 14, 19, tzinfo=SP)),
        skipped(datetime(2026, 10, 16, 19, tzinfo=SP)),
    ]
    result = summarize_adherence(
        routine,
        occurrences,
        period_start=datetime(2026, 10, 12, tzinfo=SP),
        period_end=datetime(2026, 10, 19, tzinfo=SP),
    )
    assert result.planned == 3
    assert result.done == 2
    assert result.skipped == 1
    assert result.completion == pytest.approx(2 / 3)


def test_adherence_has_no_streak() -> None:
    """§5.9.4 é explícito: sem streak, medalha nem ofensiva.

    O motivo prático: streak transforma uma falta em perda de
    progresso, o que faz a pessoa abandonar a série depois de um dia
    ruim — o oposto do que o produto deveria causar.
    """
    routine = academia()
    result = summarize_adherence(
        routine,
        [done(datetime(2026, 10, 12, 19, tzinfo=SP))],
        period_start=datetime(2026, 10, 12, tzinfo=SP),
        period_end=datetime(2026, 10, 19, tzinfo=SP),
    )
    for forbidden in ("streak", "best_streak", "badge", "medal"):
        assert not hasattr(result, forbidden), (
            f"Adherence não deve ter '{forbidden}' (§5.9.4)"
        )


def test_moved_occurrence_is_not_counted_as_skipped() -> None:
    """Adaptar-se não é falhar.

    "Movi a academia de terça pra quinta" aparece como `moved`, com
    coluna própria no relatório. Somar com as puladas puniria quem se
    adaptou em vez de desistir.
    """
    routine = academia()
    occurrences = [
        done(datetime(2026, 10, 12, 19, tzinfo=SP)),
        Occurrence(
            routine_id="r1",
            routine_title="Academia",
            start=datetime(2026, 10, 14, 19, tzinfo=SP),
            end=datetime(2026, 10, 14, 20, tzinfo=SP),
            status=OccurrenceStatus.MOVED,
            moved_to=datetime(2026, 10, 15, 19, tzinfo=SP),
        ),
    ]
    result = summarize_adherence(
        routine,
        occurrences,
        period_start=datetime(2026, 10, 12, tzinfo=SP),
        period_end=datetime(2026, 10, 19, tzinfo=SP),
    )
    assert result.moved == 1
    assert result.skipped == 0


def test_adherence_uses_actual_minutes_when_present() -> None:
    routine = academia()
    result = summarize_adherence(
        routine,
        [done(datetime(2026, 10, 12, 19, tzinfo=SP), actual=35)],
        period_start=datetime(2026, 10, 12, tzinfo=SP),
        period_end=datetime(2026, 10, 19, tzinfo=SP),
    )
    assert result.total_minutes_done == 35


def test_no_target_is_absence_not_failure() -> None:
    """Sem meta declarada, `meets_target` é None, não False."""
    routine = Routine(
        id="r3",
        title="Ler",
        rrule="FREQ=DAILY",
        duration=timedelta(minutes=30),
        started_at=datetime(2026, 10, 12, 22, tzinfo=SP),
        target_per_week=None,
    )
    result = summarize_adherence(
        routine,
        [],
        period_start=datetime(2026, 10, 12, tzinfo=SP),
        period_end=datetime(2026, 10, 19, tzinfo=SP),
    )
    assert result.meets_target is None


# ---------------------------------------------------------------
# Padrão no que foi pulado (§5.9.9)
# ---------------------------------------------------------------
def test_one_skip_is_not_a_pattern() -> None:
    """Dizer "você sempre pula de manhã" após uma falta é apressado."""
    patterns = find_skip_patterns([skipped(datetime(2026, 10, 12, 7, tzinfo=SP))])
    assert patterns == []


def test_morning_skips_are_found() -> None:
    """O caso literal de §5.9.9: as puladas eram as de manhã."""
    occurrences = [
        skipped(datetime(2026, 10, 12, 7, tzinfo=SP)),
        skipped(datetime(2026, 10, 14, 7, tzinfo=SP)),
        skipped(datetime(2026, 10, 16, 7, tzinfo=SP)),
        done(datetime(2026, 10, 13, 15, tzinfo=SP)),
        done(datetime(2026, 10, 15, 15, tzinfo=SP)),
        done(datetime(2026, 10, 17, 15, tzinfo=SP)),
    ]
    patterns = find_skip_patterns(occurrences)
    assert patterns, "o padrão de manhã deveria ser encontrado"
    top = patterns[0]
    assert top.kind == "hour"
    assert "06h" in top.label or "07h" in top.label
    assert top.skipped == 3
    assert "puladas" in describe_pattern(top)


def test_balanced_history_yields_no_pattern() -> None:
    """Padrão fraco apresentado como descoberta vira ruído."""
    occurrences = [
        done(datetime(2026, 10, 12, 7, tzinfo=SP)),
        skipped(datetime(2026, 10, 14, 7, tzinfo=SP)),
        done(datetime(2026, 10, 16, 7, tzinfo=SP)),
        done(datetime(2026, 10, 13, 15, tzinfo=SP)),
        skipped(datetime(2026, 10, 15, 15, tzinfo=SP)),
        done(datetime(2026, 10, 17, 15, tzinfo=SP)),
    ]
    assert find_skip_patterns(occurrences) == []


def test_patterns_are_sorted_by_strength() -> None:
    occurrences = [
        # Manhã: 3 de 3 puladas.
        skipped(datetime(2026, 10, 12, 7, tzinfo=SP)),
        skipped(datetime(2026, 10, 14, 7, tzinfo=SP)),
        skipped(datetime(2026, 10, 16, 7, tzinfo=SP)),
        # Noite: 2 de 3 puladas.
        skipped(datetime(2026, 10, 12, 22, tzinfo=SP)),
        skipped(datetime(2026, 10, 14, 22, tzinfo=SP)),
        done(datetime(2026, 10, 16, 22, tzinfo=SP)),
    ]
    patterns = find_skip_patterns(occurrences)
    assert len(patterns) >= 2
    assert patterns[0].ratio >= patterns[1].ratio


# ---------------------------------------------------------------
# Sobrecarga (§5.9.6)
# ---------------------------------------------------------------
def _slot(day: int, hour: int, hours: int = 2) -> BusyInterval:
    start = datetime(2026, 10, day, hour, tzinfo=SP)
    return BusyInterval(start=start, end=start + timedelta(hours=hours))


def test_overload_options_are_capped_at_three() -> None:
    """Mais de 3 é transferir o trabalho de decidir (§5.9.6)."""
    displaced = an_event(day=13, hour=14, flexibility=Flexibility.FLEXIBLE)
    options = build_overload_options(
        displaced,
        [_slot(d, 14) for d in (14, 15, 16, 17, 18)],
        same_day_load={},
    )
    assert 1 <= len(options) <= 3


def test_every_option_declares_its_cost() -> None:
    """"Dá" sozinho é resposta incompleta (§5.9.6)."""
    displaced = an_event(day=13, hour=14, flexibility=Flexibility.FLEXIBLE)
    options = build_overload_options(
        displaced, [_slot(14, 14), _slot(16, 14)], same_day_load={}
    )
    assert options
    assert all(o.cost.strip() for o in options)


def test_exactly_one_option_is_recommended() -> None:
    """"Você decide" sem opinião é covardia de assistente (§5.9.6)."""
    displaced = an_event(day=13, hour=14, flexibility=Flexibility.FLEXIBLE)
    options = build_overload_options(
        displaced, [_slot(14, 14), _slot(16, 14)], same_day_load={}
    )
    assert sum(1 for o in options if o.recommended) == 1


def test_same_day_option_mentions_stacking() -> None:
    """A opção A de §5.9.6: mesmo dia, mas 3 blocos seguidos."""
    displaced = an_event(day=13, hour=14, flexibility=Flexibility.FLEXIBLE)
    options = build_overload_options(
        displaced,
        [_slot(13, 19)],
        same_day_load={displaced.start.date(): 2},
    )
    assert options
    assert "blocos seguidos" in options[0].cost


def test_slot_too_short_is_not_offered() -> None:
    displaced = an_event(
        day=13, hour=14, hours=3, flexibility=Flexibility.FLEXIBLE
    )
    options = build_overload_options(
        displaced, [_slot(14, 14, hours=1)], same_day_load={}
    )
    assert options == []


def test_load_by_day_counts_blocks_not_minutes() -> None:
    """O cansaço de ter 4 coisas marcadas não é proporcional à duração."""
    events = [
        an_event(day=14, hour=9, hours=1),
        an_event(day=14, hour=11, hours=4),
        an_event(day=15, hour=9, hours=1),
    ]
    loads = load_by_day(events)
    assert loads[datetime(2026, 10, 14, tzinfo=SP).date()] == 2
    assert loads[datetime(2026, 10, 15, tzinfo=SP).date()] == 1
