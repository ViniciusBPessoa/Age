"""Suíte de contrato de `CalendarProvider`.

ARQUITETURA.md §10.1. A mesma suíte roda contra todo adaptador. Um
adaptador novo só é aceito se passar aqui — é assim que "pode trocar o
provedor" deixa de ser promessa e passa a ser fato verificado.

No M2 roda contra `InMemoryCalendar`. `LocalCalendar` precisa de banco e
é coberto pelos testes de integração; `GoogleCalendar` entra quando o
adaptador existir, e a suíte já está escrita para recebê-lo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

# Importar registra cada adaptador no registry (§3.2).
from app.adapters.calendar import in_memory, local  # noqa: F401
from app.adapters.calendar.registry import (
    available_calendar_providers,
    is_registered,
)
from app.ports.calendar import (
    BusyInterval,
    CalendarProvider,
    ConcurrencyConflict,
    Event,
    EventNotFound,
    Flexibility,
    NotSupported,
    SyncCapability,
    SyncCursor,
)

SP = ZoneInfo("America/Sao_Paulo")


def offline_providers() -> list[CalendarProvider]:
    """Provedores que rodam sem rede nem banco."""
    from app.adapters.calendar.in_memory import InMemoryCalendar

    return [InMemoryCalendar()]


def sample_event(
    *,
    calendar_id: str = "primary",
    title: str = "Reunião do TCC",
    day: int = 15,
    hour: int = 14,
    duration_h: int = 1,
) -> Event:
    start = datetime(2026, 10, day, hour, tzinfo=SP)
    return Event(
        id=None,
        calendar_id=calendar_id,
        title=title,
        start=start,
        end=start + timedelta(hours=duration_h),
    )


# ---------------------------------------------------------------
# Registry
# ---------------------------------------------------------------
def test_expected_providers_registered() -> None:
    for key in ("in_memory", "local"):
        assert is_registered(key), f"provedor '{key}' não registrado"


def test_local_is_available() -> None:
    """O nativo precisa existir: é o default de conta nova (§3.2.1)."""
    keys = {p["key"] for p in available_calendar_providers()}
    assert "local" in keys


def test_unknown_provider_raises() -> None:
    from app.adapters.calendar.registry import get_calendar_class

    with pytest.raises(NotSupported):
        get_calendar_class("provedor-inexistente")


# ---------------------------------------------------------------
# Validação da entidade — a porta recusa dado inválido
# ---------------------------------------------------------------
def test_event_rejects_naive_datetime() -> None:
    """Datetime sem fuso é a causa raiz de quase todo bug de agenda.

    A porta recusa na construção, não na gravação: o erro aparece onde
    o dado foi montado, não três camadas depois.
    """
    with pytest.raises(ValueError, match="timezone"):
        Event(
            id=None,
            calendar_id="primary",
            title="sem fuso",
            start=datetime(2026, 10, 15, 14),  # naive
            end=datetime(2026, 10, 15, 15),
        )


def test_event_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="antes de start"):
        Event(
            id=None,
            calendar_id="primary",
            title="invertido",
            start=datetime(2026, 10, 15, 15, tzinfo=SP),
            end=datetime(2026, 10, 15, 14, tzinfo=SP),
        )


def test_busy_interval_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone"):
        BusyInterval(
            start=datetime(2026, 10, 15, 14), end=datetime(2026, 10, 15, 15)
        )


def test_overlaps_is_exclusive_at_boundaries() -> None:
    """Eventos que só encostam não se sobrepõem.

    Importa: 14h–15h e 15h–16h são compatíveis, e tratá-los como
    conflito recusaria agendamentos perfeitamente válidos.
    """
    a = BusyInterval(
        start=datetime(2026, 10, 15, 14, tzinfo=SP),
        end=datetime(2026, 10, 15, 15, tzinfo=SP),
    )
    b = BusyInterval(
        start=datetime(2026, 10, 15, 15, tzinfo=SP),
        end=datetime(2026, 10, 15, 16, tzinfo=SP),
    )
    assert not a.overlaps(b)
    assert a.touches(b)  # encosta, para fins de fusão


# ---------------------------------------------------------------
# O contrato
# ---------------------------------------------------------------
@pytest.mark.parametrize("provider", offline_providers(), ids=lambda p: p.key)
class TestCalendarProviderContract:
    async def test_list_calendars_has_primary(
        self, provider: CalendarProvider
    ) -> None:
        calendars = await provider.list_calendars()
        assert calendars, "provedor deve ter ao menos um calendário"
        assert any(c.is_primary for c in calendars)
        assert all(c.timezone for c in calendars)
        await provider.aclose()

    async def test_create_then_read_roundtrip(
        self, provider: CalendarProvider
    ) -> None:
        created = await provider.create_event(sample_event())
        assert created.id is not None

        found = await provider.list_events(
            ["primary"],
            datetime(2026, 10, 15, 0, tzinfo=SP),
            datetime(2026, 10, 16, 0, tzinfo=SP),
        )
        assert any(e.id == created.id for e in found)
        await provider.aclose()

    async def test_timezone_aware_always(self, provider: CalendarProvider) -> None:
        """Todo evento que sai da porta tem fuso."""
        await provider.create_event(sample_event())
        events = await provider.list_events(
            ["primary"],
            datetime(2026, 10, 15, 0, tzinfo=SP),
            datetime(2026, 10, 16, 0, tzinfo=SP),
        )
        for e in events:
            assert e.start.tzinfo is not None
            assert e.end.tzinfo is not None
        await provider.aclose()

    async def test_window_filter_excludes_outside(
        self, provider: CalendarProvider
    ) -> None:
        await provider.create_event(sample_event(day=15))
        await provider.create_event(sample_event(day=20, title="Outro"))

        found = await provider.list_events(
            ["primary"],
            datetime(2026, 10, 15, 0, tzinfo=SP),
            datetime(2026, 10, 16, 0, tzinfo=SP),
        )
        assert all(e.start.day == 15 for e in found)
        await provider.aclose()

    async def test_free_busy_matches_events(
        self, provider: CalendarProvider
    ) -> None:
        """free_busy e list_events não podem divergir.

        O Scheduler consome free_busy; se ele mostrar menos ocupação que
        a realidade, o produto agenda em cima de compromisso.
        """
        await provider.create_event(sample_event(hour=10))
        await provider.create_event(sample_event(hour=14, title="Segundo"))

        start = datetime(2026, 10, 15, 0, tzinfo=SP)
        end = datetime(2026, 10, 16, 0, tzinfo=SP)

        events = await provider.list_events(["primary"], start, end)
        busy = await provider.free_busy(["primary"], start, end)

        timed = [e for e in events if not e.all_day]
        assert len(busy) == len(timed)
        for interval in busy:
            assert any(
                e.start == interval.start and e.end == interval.end
                for e in timed
            )
        await provider.aclose()

    async def test_update_changes_event(self, provider: CalendarProvider) -> None:
        created = await provider.create_event(sample_event())
        moved = Event(
            id=created.id,
            calendar_id=created.calendar_id,
            title=created.title,
            start=created.start + timedelta(hours=2),
            end=created.end + timedelta(hours=2),
        )
        updated = await provider.update_event(moved)
        assert updated.start == created.start + timedelta(hours=2)
        await provider.aclose()

    async def test_update_missing_event_raises(
        self, provider: CalendarProvider
    ) -> None:
        ghost = Event(
            id="nao-existe-999",
            calendar_id="primary",
            title="fantasma",
            start=datetime(2026, 10, 15, 14, tzinfo=SP),
            end=datetime(2026, 10, 15, 15, tzinfo=SP),
        )
        with pytest.raises(EventNotFound):
            await provider.update_event(ghost)
        await provider.aclose()

    async def test_etag_conflict_raises(self, provider: CalendarProvider) -> None:
        """Escrita concorrente falha em vez de sobrescrever em silêncio.

        É o que permite ao PlanExecutor revalidar antes de aplicar
        (§5.8): aprovar um plano e descobrir que a agenda mudou é
        melhor que atropelar a mudança de alguém.
        """
        if not provider.capabilities.supports_etag_concurrency:
            pytest.skip("provedor sem controle de concorrência")

        created = await provider.create_event(sample_event())
        # Alguém mais atualiza primeiro.
        await provider.update_event(
            Event(
                id=created.id,
                calendar_id=created.calendar_id,
                title="Mudado por outro",
                start=created.start,
                end=created.end,
            )
        )
        # Nossa escrita usa o etag velho.
        with pytest.raises(ConcurrencyConflict):
            await provider.update_event(
                Event(
                    id=created.id,
                    calendar_id=created.calendar_id,
                    title="Minha mudança",
                    start=created.start,
                    end=created.end,
                ),
                expected_etag=created.etag,
            )
        await provider.aclose()

    async def test_delete_removes_event(self, provider: CalendarProvider) -> None:
        created = await provider.create_event(sample_event())
        await provider.delete_event("primary", created.id or "")

        found = await provider.list_events(
            ["primary"],
            datetime(2026, 10, 15, 0, tzinfo=SP),
            datetime(2026, 10, 16, 0, tzinfo=SP),
        )
        assert all(e.id != created.id for e in found)
        await provider.aclose()

    async def test_delete_is_idempotent(self, provider: CalendarProvider) -> None:
        """Apagar o que não existe não é erro.

        Importa para a reversão compensatória (§5.8): o undo pode tentar
        apagar algo que já saiu, e isso não deve derrubar a operação.
        """
        created = await provider.create_event(sample_event())
        await provider.delete_event("primary", created.id or "")
        await provider.delete_event("primary", created.id or "")  # sem erro
        await provider.delete_event("primary", "jamais-existiu")
        await provider.aclose()

    async def test_sync_cursor_roundtrip(self, provider: CalendarProvider) -> None:
        if provider.capabilities.sync == SyncCapability.POLL_ONLY:
            pytest.skip("provedor só faz polling")

        first = await provider.sync("primary", None)
        assert first.next_cursor is not None

        await provider.create_event(sample_event(title="Novo após sync"))

        second = await provider.sync("primary", first.next_cursor)
        assert any(e.title == "Novo após sync" for e in second.changed)
        await provider.aclose()

    async def test_invalid_cursor_requests_full_resync(
        self, provider: CalendarProvider
    ) -> None:
        """Cursor ilegível pede resync, não adivinha.

        É o caminho que PRECISA estar testado: acontece depois de
        qualquer indisponibilidade longa do provedor.
        """
        if provider.capabilities.sync == SyncCapability.POLL_ONLY:
            pytest.skip("provedor só faz polling")

        result = await provider.sync(
            "primary",
            SyncCursor(opaque="lixo-nao-parseavel", issued_at=datetime.now(UTC)),
        )
        assert result.full_resync_required is True
        await provider.aclose()

    async def test_declared_capabilities_are_truthful(
        self, provider: CalendarProvider
    ) -> None:
        """Declarar é prometer.

        Se o provedor diz que suporta recorrência, recorrência funciona.
        Se não diz, levanta NotSupported — nunca falha em silêncio.
        """
        caps = provider.capabilities

        if caps.supports_recurrence:
            created = await provider.create_event(
                Event(
                    id=None,
                    calendar_id="primary",
                    title="Academia",
                    start=datetime(2026, 10, 15, 19, tzinfo=SP),
                    end=datetime(2026, 10, 15, 20, tzinfo=SP),
                    recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR",
                )
            )
            assert created.recurrence is not None

        if caps.sync != SyncCapability.PUSH:
            with pytest.raises(NotSupported):
                await provider.watch("primary", "https://exemplo.com/hook")

        await provider.aclose()

    async def test_flexibility_round_trips(
        self, provider: CalendarProvider
    ) -> None:
        """Flexibilidade é metadado local, mas tem de sobreviver (§5.6)."""
        created = await provider.create_event(
            Event(
                id=None,
                calendar_id="primary",
                title="Academia",
                start=datetime(2026, 10, 15, 19, tzinfo=SP),
                end=datetime(2026, 10, 15, 20, tzinfo=SP),
                flexibility=Flexibility.FLEXIBLE,
            )
        )
        assert created.flexibility == Flexibility.FLEXIBLE
        await provider.aclose()

    async def test_aclose_is_idempotent(self, provider: CalendarProvider) -> None:
        await provider.aclose()
        await provider.aclose()


# ---------------------------------------------------------------
# Default de flexibilidade: o viés para rígido (§5.6)
# ---------------------------------------------------------------
def test_flexibility_defaults_to_fixed() -> None:
    """Na dúvida, não mexe.

    Mover a consulta médica de alguém por heurística errada é o tipo de
    erro que destrói a confiança no produto de uma vez.
    """
    event = sample_event()
    assert event.flexibility == Flexibility.FIXED
