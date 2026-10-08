"""Calendário em memória — para testes.

ARQUITETURA.md §3.2.4. Implementa o contrato inteiro sem rede nem banco,
o que permite rodar a suíte de contrato e os testes de integração sem
nenhuma conta externa.

Suporta capacidades plenas de propósito: é o provedor de referência
contra o qual os pobres são comparados.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.adapters.calendar.registry import register_calendar
from app.ports.calendar import (
    BusyInterval,
    CalendarCapabilities,
    CalendarProvider,
    CalendarRef,
    ConcurrencyConflict,
    Event,
    EventNotFound,
    SyncCapability,
    SyncCursor,
    SyncResult,
)

_CAPS = CalendarCapabilities(
    supports_recurrence=True,
    supports_attendees=True,
    supports_etag_concurrency=True,
    supports_multiple_calendars=True,
    supports_colors=True,
    sync=SyncCapability.INCREMENTAL,
    max_window_days=None,
)


@register_calendar
class InMemoryCalendar(CalendarProvider):
    key = "in_memory"
    display_name = "Em memória (testes)"

    def __init__(self, *, timezone: str = "America/Sao_Paulo") -> None:
        self._timezone = timezone
        self._calendars: dict[str, CalendarRef] = {}
        self._events: dict[str, Event] = {}
        self._deleted: list[tuple[str, datetime]] = []
        # Contador monotônico serve de cursor: simples e suficiente para
        # exercitar o caminho de sync incremental.
        self._revision = 0
        self._event_revision: dict[str, int] = {}

        primary = CalendarRef(
            id="primary",
            name="Principal",
            timezone=timezone,
            is_primary=True,
            writable=True,
        )
        self._calendars[primary.id] = primary

    @property
    def capabilities(self) -> CalendarCapabilities:
        return _CAPS

    # -----------------------------------------------------------
    async def list_calendars(self) -> list[CalendarRef]:
        return list(self._calendars.values())

    def add_calendar(self, ref: CalendarRef) -> None:
        """Auxiliar de teste, fora do contrato."""
        self._calendars[ref.id] = ref

    async def list_events(
        self, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[Event]:
        wanted = set(calendar_ids)
        return sorted(
            (
                e
                for e in self._events.values()
                if e.calendar_id in wanted and e.start < end and e.end > start
            ),
            key=lambda e: e.start,
        )

    async def free_busy(
        self, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[BusyInterval]:
        events = await self.list_events(calendar_ids, start, end)
        return [
            BusyInterval(start=max(e.start, start), end=min(e.end, end))
            for e in events
            if not e.all_day
        ]

    async def create_event(self, event: Event) -> Event:
        event_id = event.id or f"mem_{uuid4().hex[:12]}"
        self._revision += 1
        stored = Event(
            id=event_id,
            calendar_id=event.calendar_id,
            title=event.title,
            start=event.start,
            end=event.end,
            description=event.description,
            location=event.location,
            all_day=event.all_day,
            recurrence=event.recurrence,
            recurring_event_id=event.recurring_event_id,
            attendees=event.attendees,
            source=event.source,
            etag=f"etag-{self._revision}",
            flexibility=event.flexibility,
        )
        self._events[event_id] = stored
        self._event_revision[event_id] = self._revision
        return stored

    async def update_event(
        self, event: Event, expected_etag: str | None = None
    ) -> Event:
        if event.id is None or event.id not in self._events:
            raise EventNotFound(f"evento {event.id} não existe")

        current = self._events[event.id]
        if expected_etag is not None and current.etag != expected_etag:
            raise ConcurrencyConflict(
                f"evento {event.id} mudou desde a leitura "
                f"(etag {current.etag} != {expected_etag})"
            )

        self._revision += 1
        updated = Event(
            id=event.id,
            calendar_id=event.calendar_id,
            title=event.title,
            start=event.start,
            end=event.end,
            description=event.description,
            location=event.location,
            all_day=event.all_day,
            recurrence=event.recurrence,
            recurring_event_id=event.recurring_event_id,
            attendees=event.attendees,
            source=event.source,
            etag=f"etag-{self._revision}",
            flexibility=event.flexibility,
        )
        self._events[event.id] = updated
        self._event_revision[event.id] = self._revision
        return updated

    async def delete_event(
        self, calendar_id: str, event_id: str, expected_etag: str | None = None
    ) -> None:
        current = self._events.get(event_id)
        if current is None:
            # Idempotente: apagar o que não existe não é erro.
            return
        if expected_etag is not None and current.etag != expected_etag:
            raise ConcurrencyConflict(f"evento {event_id} mudou desde a leitura")

        self._revision += 1
        del self._events[event_id]
        self._event_revision.pop(event_id, None)
        self._deleted.append((event_id, datetime.now(UTC)))

    async def sync(
        self, calendar_id: str, cursor: SyncCursor | None
    ) -> SyncResult:
        since = 0
        if cursor is not None:
            try:
                since = int(cursor.opaque)
            except ValueError:
                # Cursor ilegível: pede resync completo em vez de
                # adivinhar. É o caminho que precisa estar testado.
                return SyncResult(full_resync_required=True)

        changed = tuple(
            e
            for e in self._events.values()
            if e.calendar_id == calendar_id
            and self._event_revision.get(e.id or "", 0) > since
        )
        deleted = tuple(eid for eid, _ in self._deleted)

        return SyncResult(
            changed=changed,
            deleted_ids=deleted if since > 0 else (),
            next_cursor=SyncCursor(
                opaque=str(self._revision), issued_at=datetime.now(UTC)
            ),
        )

    # -----------------------------------------------------------
    def seed(self, events: list[Event]) -> None:
        """Popula para testes, sem passar pelo create."""
        for event in events:
            self._revision += 1
            eid = event.id or f"mem_{uuid4().hex[:12]}"
            self._events[eid] = Event(
                id=eid,
                calendar_id=event.calendar_id,
                title=event.title,
                start=event.start,
                end=event.end,
                description=event.description,
                location=event.location,
                all_day=event.all_day,
                recurrence=event.recurrence,
                attendees=event.attendees,
                source=event.source,
                etag=f"etag-{self._revision}",
                flexibility=event.flexibility,
            )
            self._event_revision[eid] = self._revision
