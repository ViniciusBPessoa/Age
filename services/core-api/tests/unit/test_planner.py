"""Testes do Planner.

ARQUITETURA.md §5.5. O caso que originou o projeto. Os testes que mais
importam são os de comportamento honesto: respeitar pré-requisito,
nunca agendar no dia da prova, e reportar o que não couber em vez de
cortar em silêncio.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.planner import (
    SessionKind,
    StudyPlan,
    Topic,
    add_spaced_reviews,
    allocate_sessions,
    build_study_plan,
    describe_shortfall,
    order_by_prerequisites,
    slice_topics,
)
from app.domain.scheduler import DailyWindow, SchedulingPolicy
from app.ports.calendar import BusyInterval

SP = ZoneInfo("America/Sao_Paulo")
YEAR = 2027


def dt(day: int, hour: int, minute: int = 0, *, month: int = 5) -> datetime:
    return datetime(YEAR, month, day, hour, minute, tzinfo=SP)


def policy(**kw) -> SchedulingPolicy:
    base = {
        "timezone": "America/Sao_Paulo",
        "working_hours": {d: (DailyWindow(time(8), time(22)),) for d in range(7)},
        "buffer": timedelta(0),
        "session_min": timedelta(minutes=50),
        "session_max": timedelta(minutes=90),
        "max_hours_per_day": timedelta(hours=6),
    }
    return SchedulingPolicy(**{**base, **kw})


def algebra_topics() -> list[Topic]:
    """Os tópicos de §5.5, com os pré-requisitos reais da disciplina."""
    return [
        Topic("Sistemas lineares", timedelta(minutes=90), 0),
        Topic("Espaços vetoriais", timedelta(hours=2), 1, ("Sistemas lineares",)),
        Topic("Base e dimensão", timedelta(hours=2), 2, ("Espaços vetoriais",)),
        Topic(
            "Transformações lineares",
            timedelta(minutes=90),
            3,
            ("Base e dimensão",),
        ),
        Topic("Autovalores", timedelta(hours=2), 4, ("Transformações lineares",)),
        Topic("Revisão e exercícios", timedelta(hours=2), 5),
    ]


# ---------------------------------------------------------------
# Fatiamento
# ---------------------------------------------------------------
def test_short_topic_becomes_one_session() -> None:
    sessions = slice_topics(
        [Topic("Curto", timedelta(minutes=60), 0)], policy()
    )
    assert len(sessions) == 1
    assert sessions[0].duration == timedelta(minutes=60)


def test_long_topic_is_split() -> None:
    """Tópico de 3h não vira uma sessão de 3h."""
    sessions = slice_topics(
        [Topic("Longo", timedelta(hours=3), 0)], policy()
    )
    assert len(sessions) >= 2
    assert all(s.duration <= policy().session_max for s in sessions)
    # O total é preservado: fatiar não perde tempo de estudo.
    assert sum((s.duration for s in sessions), timedelta()) == timedelta(hours=3)


def test_split_parts_are_balanced() -> None:
    """Não sobra uma parte de 10 minutos no fim.

    Distribuir igualmente importa: uma sessão de 10 min no fim é
    inútil e parece descuido.
    """
    sessions = slice_topics(
        [Topic("Tópico", timedelta(minutes=100), 0)], policy()
    )
    durations = {s.duration for s in sessions}
    assert len(durations) == 1, "todas as partes devem ter a mesma duração"


def test_split_sessions_are_numbered() -> None:
    sessions = slice_topics(
        [Topic("Longo", timedelta(hours=3), 0)], policy()
    )
    assert sessions[0].part == 1
    assert sessions[0].of_parts == len(sessions)
    assert sessions[-1].part == len(sessions)


def test_zero_duration_topic_is_skipped() -> None:
    sessions = slice_topics([Topic("Vazio", timedelta(0), 0)], policy())
    assert sessions == []


def test_very_short_topic_is_not_padded() -> None:
    """Tópico de 20 min vira sessão de 20 min, não de 50.

    Esticar para o mínimo seria inventar tempo de estudo.
    """
    sessions = slice_topics(
        [Topic("Rapidinho", timedelta(minutes=20), 0)], policy()
    )
    assert len(sessions) == 1
    assert sessions[0].duration == timedelta(minutes=20)


# ---------------------------------------------------------------
# Pré-requisitos
# ---------------------------------------------------------------
def test_prerequisites_are_respected() -> None:
    """Agendar autovalores antes de espaços vetoriais é inútil."""
    ordered = order_by_prerequisites(algebra_topics())
    names = [t.name for t in ordered]

    assert names.index("Sistemas lineares") < names.index("Espaços vetoriais")
    assert names.index("Espaços vetoriais") < names.index("Base e dimensão")
    assert names.index("Base e dimensão") < names.index("Transformações lineares")
    assert names.index("Transformações lineares") < names.index("Autovalores")


def test_prerequisite_declared_out_of_order_is_fixed() -> None:
    """O modelo pode declarar na ordem errada; a topológica corrige."""
    topics = [
        Topic("Avançado", timedelta(hours=1), 0, ("Básico",)),
        Topic("Básico", timedelta(hours=1), 1),
    ]
    names = [t.name for t in order_by_prerequisites(topics)]
    assert names == ["Básico", "Avançado"]


def test_cycle_falls_back_to_declared_order() -> None:
    """Ciclo não derruba o plano.

    O modelo pode declarar dependência mútua por descuido, e perder o
    plano inteiro por isso seria desproporcional.
    """
    topics = [
        Topic("A", timedelta(hours=1), 0, ("B",)),
        Topic("B", timedelta(hours=1), 1, ("A",)),
    ]
    ordered = order_by_prerequisites(topics)
    assert len(ordered) == 2
    assert [t.name for t in ordered] == ["A", "B"]


def test_unknown_prerequisite_is_ignored() -> None:
    """Pré-requisito que não está na lista não trava nada."""
    topics = [
        Topic("Único", timedelta(hours=1), 0, ("Inexistente",)),
    ]
    ordered = order_by_prerequisites(topics)
    assert len(ordered) == 1


# ---------------------------------------------------------------
# Alocação
# ---------------------------------------------------------------
def test_allocates_all_sessions_in_empty_agenda() -> None:
    sessions = slice_topics(algebra_topics(), policy())
    placed, unplaced = allocate_sessions(
        sessions, [], dt(10, 8), dt(19, 22), policy(), deadline=dt(20, 14)
    )
    assert not unplaced
    assert len(placed) == len(sessions)


def test_allocated_sessions_never_overlap() -> None:
    """A invariante central, agora no nível do plano."""
    sessions = slice_topics(algebra_topics(), policy())
    placed, _ = allocate_sessions(
        sessions, [], dt(10, 8), dt(19, 22), policy(), deadline=dt(20, 14)
    )
    for a, b in zip(
        sorted(placed, key=lambda s: s.start),
        sorted(placed, key=lambda s: s.start)[1:],
        strict=False,
    ):
        assert not a.as_busy().overlaps(b.as_busy())


def test_allocation_respects_existing_events() -> None:
    busy = [BusyInterval(start=dt(12, 8), end=dt(12, 22))]  # dia 12 cheio
    sessions = slice_topics(algebra_topics(), policy())
    placed, _ = allocate_sessions(
        sessions, busy, dt(10, 8), dt(19, 22), policy(), deadline=dt(20, 14)
    )
    for session in placed:
        assert not session.as_busy().overlaps(busy[0])


def test_sessions_follow_topic_order_in_time() -> None:
    """Pré-requisito antes no tempo, não só na lista.

    Ordenar a lista não basta: as sessões precisam ser agendadas em
    ordem cronológica compatível.
    """
    topics = [
        Topic("Primeiro", timedelta(hours=1), 0),
        Topic("Segundo", timedelta(hours=1), 1, ("Primeiro",)),
    ]
    sessions = slice_topics(order_by_prerequisites(topics), policy())
    placed, _ = allocate_sessions(
        sessions, [], dt(10, 8), dt(15, 22), policy()
    )
    first = next(s for s in placed if s.topic_name == "Primeiro")
    second = next(s for s in placed if s.topic_name == "Segundo")
    assert first.end <= second.start


def test_impossible_plan_reports_unplaced() -> None:
    """O que não cabe é REPORTADO, não cortado em silêncio (§5.5 passo 6).

    Este é o teste de honestidade do produto: o assistente nunca
    entrega um plano que parece completo mas não é.
    """
    # 11h de estudo numa janela de um dia com 6h de limite.
    sessions = slice_topics(algebra_topics(), policy())
    placed, unplaced = allocate_sessions(
        sessions, [], dt(10, 8), dt(10, 22), policy(), deadline=dt(11, 8)
    )
    assert unplaced, "deveria reportar o que não caiu"
    assert len(placed) < len(sessions)


def test_shortens_session_before_giving_up() -> None:
    """Uma sessão de 50 min que acontece vale mais que 90 que não cabe."""
    # Lacuna de exatamente 1h; a sessão pedida é de 90 min.
    busy = [
        BusyInterval(start=dt(10, 8), end=dt(10, 14)),
        BusyInterval(start=dt(10, 15), end=dt(10, 22)),
    ]
    sessions = slice_topics([Topic("Apertado", timedelta(minutes=90), 0)], policy())
    placed, unplaced = allocate_sessions(
        sessions, busy, dt(10, 8), dt(10, 22), policy()
    )
    assert placed, "deveria encurtar em vez de desistir"
    assert placed[0].duration >= policy().session_min


# ---------------------------------------------------------------
# Revisão espaçada
# ---------------------------------------------------------------
def test_adds_spaced_reviews() -> None:
    sessions = slice_topics([Topic("Tópico", timedelta(hours=1), 0)], policy())
    placed, _ = allocate_sessions(
        sessions, [], dt(10, 8), dt(25, 22), policy()
    )
    reviews = add_spaced_reviews(placed, [], dt(25, 22), policy())

    assert reviews
    assert all(r.kind == SessionKind.REVIEW for r in reviews)
    # Revisão vem depois da sessão de estudo.
    assert all(r.start > placed[0].end for r in reviews)


def test_reviews_are_short() -> None:
    """Relembrar custa menos que aprender."""
    sessions = slice_topics([Topic("Tópico", timedelta(hours=2), 0)], policy())
    placed, _ = allocate_sessions(sessions, [], dt(10, 8), dt(25, 22), policy())
    reviews = add_spaced_reviews(placed, [], dt(25, 22), policy())
    assert all(r.duration <= timedelta(minutes=30) for r in reviews)


def test_reviews_respect_deadline() -> None:
    sessions = slice_topics([Topic("Tópico", timedelta(hours=1), 0)], policy())
    placed, _ = allocate_sessions(sessions, [], dt(10, 8), dt(25, 22), policy())
    deadline = dt(12, 8)
    reviews = add_spaced_reviews(
        placed, [], dt(25, 22), policy(), deadline=deadline
    )
    assert all(r.end <= deadline for r in reviews)


def test_reviews_do_not_overlap_study() -> None:
    sessions = slice_topics(algebra_topics(), policy())
    placed, _ = allocate_sessions(sessions, [], dt(10, 8), dt(30, 22), policy())
    reviews = add_spaced_reviews(placed, [], dt(30, 22), policy())

    for review in reviews:
        for study in placed:
            assert not review.as_busy().overlaps(study.as_busy())


# ---------------------------------------------------------------
# Plano completo — o caso de §5.5
# ---------------------------------------------------------------
def test_algebra_linear_end_to_end() -> None:
    """O caso que originou o projeto.

    "Preciso estudar Álgebra Linear, a prova é dia 20."
    """
    plan = build_study_plan(
        subject="Álgebra Linear",
        topics=algebra_topics(),
        busy=[],
        window_start=dt(7, 8),
        window_end=dt(20, 22),
        policy=policy(),
        deadline=dt(20, 14),
    )

    assert plan.fits, f"deveria caber: {plan.unplaced}"
    assert plan.total_estimated == timedelta(hours=11, minutes=0)
    assert plan.sessions

    study = [s for s in plan.sessions if s.kind == SessionKind.STUDY]
    assert len(study) >= 6, "11h devem virar várias sessões"

    # Nunca no dia da prova (§5.5).
    assert all(s.start.date() < plan.deadline.date() for s in plan.sessions)

    # Há folga antes do prazo: é a informação que permite confiar.
    assert plan.slack_days is not None


def test_plan_never_schedules_on_exam_day() -> None:
    plan = build_study_plan(
        subject="Cálculo",
        topics=[Topic("Integrais", timedelta(hours=2), 0)],
        busy=[],
        window_start=dt(18, 8),
        window_end=dt(20, 22),
        policy=policy(),
        deadline=dt(20, 14),
    )
    assert all(s.start.date() < dt(20, 0).date() for s in plan.sessions)


def test_plan_titles_include_subject() -> None:
    """"Espaços vetoriais" sozinho não diz de que matéria é."""
    plan = build_study_plan(
        subject="Álgebra Linear",
        topics=[Topic("Espaços vetoriais", timedelta(hours=1), 0)],
        busy=[],
        window_start=dt(10, 8),
        window_end=dt(15, 22),
        policy=policy(),
    )
    titles = [s.title(plan.subject) for s in plan.sessions]
    assert all("Álgebra Linear" in t for t in titles)


def test_plan_marks_review_in_title() -> None:
    plan = build_study_plan(
        subject="Cálculo",
        topics=[Topic("Limites", timedelta(hours=1), 0)],
        busy=[],
        window_start=dt(10, 8),
        window_end=dt(25, 22),
        policy=policy(),
    )
    reviews = [s for s in plan.sessions if s.kind == SessionKind.REVIEW]
    if reviews:
        assert "revisão" in reviews[0].title(plan.subject)


def test_empty_topics_gives_empty_plan() -> None:
    plan = build_study_plan(
        subject="Nada",
        topics=[],
        busy=[],
        window_start=dt(10, 8),
        window_end=dt(15, 22),
        policy=policy(),
    )
    assert plan.sessions == []
    assert plan.fits


def test_shortfall_message_is_honest_and_actionable() -> None:
    """A mensagem de §5.5 passo 6: diz o que faltou e oferece saídas."""
    plan = StudyPlan(
        subject="Álgebra Linear",
        sessions=[],
        unplaced=[("Autovalores", timedelta(hours=2))],
        total_estimated=timedelta(hours=11),
        total_scheduled=timedelta(hours=9),
    )
    message = describe_shortfall(plan)

    assert "Não cabe tudo" in message
    assert "Autovalores" in message
    assert "2.0h" in message
    # Oferece as três saídas reais, não "não consegui".
    assert "estender o prazo" in message
    assert "reduzir a profundidade" in message
    assert "horário que você bloqueou" in message


def test_no_shortfall_message_when_it_fits() -> None:
    plan = StudyPlan(subject="X", sessions=[], unplaced=[])
    assert describe_shortfall(plan) == ""


def test_respects_preferred_hours() -> None:
    """Preferência de horário do usuário é respeitada na alocação."""
    plan = build_study_plan(
        subject="Cálculo",
        topics=[Topic("Limites", timedelta(hours=1), 0)],
        busy=[],
        window_start=dt(10, 8),
        window_end=dt(15, 22),
        policy=policy(preferred_hours=(DailyWindow(time(14), time(18)),)),
        include_reviews=False,
    )
    assert plan.sessions
    hour = plan.sessions[0].start.astimezone(SP).hour
    assert 14 <= hour < 18
