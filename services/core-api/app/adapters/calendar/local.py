"""Calendário nativo do Age — adaptador de primeira classe.

ARQUITETURA.md §3.2.1. **Não é um stub de teste.** É o provedor default
de uma conta nova, e existe por três razões:

1. **Onboarding sem fricção.** Dá para criar conta, conversar e ter a
   agenda montada sem passar pelo consentimento OAuth do Google.
2. **É a prova de que a porta é real.** Um adaptador que não é Google,
   em produção, usado por padrão, impede a abstração de virar fachada —
   o jeito clássico de uma porta apodrecer é ter só uma implementação de
   verdade.
3. **Foi escrito ANTES do Google**, de propósito: construir a porta
   tendo o Google como única referência é como ela acaba moldada a ele
   sem ninguém perceber.

Capacidades plenas, exceto push: não há webhook a assinar, porque o
front consulta a própria API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.calendar.registry import register_calendar
from app.ports.calendar import (
    BusyInterval,
    CalendarCapabilities,
    CalendarProvider,
    CalendarRef,
    ConcurrencyConflict,
    Event,
    EventNotFound,
    Flexibility,
    SyncCapability,
    SyncCursor,
    SyncResult,
)

_CAPS = CalendarCapabilities(
    supports_recurrence=True,
    supports_attendees=False,  # agenda pessoal, sem convidados (§1.2)
    supports_etag_concurrency=True,  # via coluna `version`
    supports_multiple_calendars=True,
    supports_colors=True,
    sync=SyncCapability.INCREMENTAL,
    max_window_days=None,
)


@register_calendar
class LocalCalendar(CalendarProvider):
    key = "local"
    display_name = "Calendário do Age"

    def __init__(self, *, db: AsyncSession, user_id: UUID) -> None:
        self._db = db
        self._user_id = user_id

    @property
    def capabilities(self) -> CalendarCapabilities:
        return _CAPS

    # -----------------------------------------------------------
    async def ensure_default_calendar(self) -> CalendarRef:
        """Cria o calendário padrão se a conta ainda não tem.

        Chamado no primeiro acesso à agenda em vez de no registro: evita
        que o cadastro falhe por causa de algo secundário.
        """
        row = (
            await self._db.execute(
                text(
                    """
                    SELECT id, name, timezone, is_default, color
                    FROM core.local_calendars
                    WHERE user_id = :uid
                    ORDER BY is_default DESC, created_at ASC
                    LIMIT 1
                    """
                ),
                {"uid": str(self._user_id)},
            )
        ).first()

        if row is not None:
            return CalendarRef(
                id=str(row[0]),
                name=row[1],
                timezone=row[2],
                is_primary=row[3],
                writable=True,
                color=row[4],
            )

        tz = (
            await self._db.execute(
                text("SELECT timezone FROM core.users WHERE id = :uid"),
                {"uid": str(self._user_id)},
            )
        ).scalar_one_or_none() or "America/Sao_Paulo"

        created = (
            await self._db.execute(
                text(
                    """
                    INSERT INTO core.local_calendars
                      (user_id, name, timezone, is_default)
                    VALUES (:uid, 'Minha agenda', :tz, true)
                    RETURNING id
                    """
                ),
                {"uid": str(self._user_id), "tz": tz},
            )
        ).scalar_one()

        return CalendarRef(
            id=str(created),
            name="Minha agenda",
            timezone=tz,
            is_primary=True,
            writable=True,
        )

    async def list_calendars(self) -> list[CalendarRef]:
        rows = await self._db.execute(
            text(
                """
                SELECT id, name, timezone, is_default, color
                FROM core.local_calendars
                WHERE user_id = :uid
                ORDER BY is_default DESC, created_at ASC
                """
            ),
            {"uid": str(self._user_id)},
        )
        found = [
            CalendarRef(
                id=str(r[0]),
                name=r[1],
                timezone=r[2],
                is_primary=r[3],
                writable=True,
                color=r[4],
            )
            for r in rows.all()
        ]
        if not found:
            return [await self.ensure_default_calendar()]
        return found

    # -----------------------------------------------------------
    async def list_events(
        self, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[Event]:
        if not calendar_ids:
            return []

        rows = await self._db.execute(
            text(
                """
                SELECT id, local_calendar_id, title, start_at, end_at,
                       all_day, description, location, recurrence,
                       flexibility, created_by_age, version
                FROM core.local_events
                WHERE user_id = :uid
                  AND local_calendar_id = ANY(:cals)
                  AND deleted_at IS NULL
                  AND start_at < :end AND end_at > :start
                ORDER BY start_at
                """
            ),
            {
                "uid": str(self._user_id),
                "cals": [str(c) for c in calendar_ids],
                "start": start,
                "end": end,
            },
        )
        return [self._to_event(r) for r in rows.all()]

    async def free_busy(
        self, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[BusyInterval]:
        """Caminho separado e mais barato: não carrega título nem descrição.

        É o que o modo privado usa (§9.3), e o que o Scheduler consome.
        """
        if not calendar_ids:
            return []

        rows = await self._db.execute(
            text(
                """
                SELECT start_at, end_at
                FROM core.local_events
                WHERE user_id = :uid
                  AND local_calendar_id = ANY(:cals)
                  AND deleted_at IS NULL
                  AND all_day = false
                  AND start_at < :end AND end_at > :start
                ORDER BY start_at
                """
            ),
            {
                "uid": str(self._user_id),
                "cals": [str(c) for c in calendar_ids],
                "start": start,
                "end": end,
            },
        )
        return [
            BusyInterval(start=max(r[0], start), end=min(r[1], end))
            for r in rows.all()
        ]

    # -----------------------------------------------------------
    async def create_event(self, event: Event) -> Event:
        row = (
            await self._db.execute(
                text(
                    """
                    INSERT INTO core.local_events
                      (user_id, local_calendar_id, title, start_at, end_at,
                       all_day, description, location, recurrence,
                       flexibility, created_by_age)
                    VALUES (:uid, :cal, :title, :start, :end, :all_day,
                            :descr, :loc, :rrule, :flex, :by_age)
                    RETURNING id, local_calendar_id, title, start_at, end_at,
                              all_day, description, location, recurrence,
                              flexibility, created_by_age, version
                    """
                ),
                {
                    "uid": str(self._user_id),
                    "cal": event.calendar_id,
                    "title": event.title,
                    "start": event.start,
                    "end": event.end,
                    "all_day": event.all_day,
                    "descr": event.description,
                    "loc": event.location,
                    "rrule": event.recurrence,
                    "flex": event.flexibility.value,
                    "by_age": event.source == "age",
                },
            )
        ).first()
        return self._to_event(row)

    async def update_event(
        self, event: Event, expected_etag: str | None = None
    ) -> Event:
        if event.id is None:
            raise EventNotFound("update exige id")

        # Concorrência otimista pela coluna `version`: o etag exposto na
        # porta é a versão em texto.
        expected_version = None
        if expected_etag is not None:
            try:
                expected_version = int(expected_etag)
            except ValueError:
                raise ConcurrencyConflict(
                    f"etag inválido para calendário local: {expected_etag!r}"
                ) from None

        row = (
            await self._db.execute(
                text(
                    """
                    UPDATE core.local_events
                    SET title = :title, start_at = :start, end_at = :end,
                        all_day = :all_day, description = :descr,
                        location = :loc, recurrence = :rrule,
                        flexibility = :flex,
                        version = version + 1, updated_at = now()
                    WHERE id = :eid AND user_id = :uid AND deleted_at IS NULL
                      AND (CAST(:expected AS integer) IS NULL
                       OR version = CAST(:expected AS integer))
                    RETURNING id, local_calendar_id, title, start_at, end_at,
                              all_day, description, location, recurrence,
                              flexibility, created_by_age, version
                    """
                ),
                {
                    "eid": event.id,
                    "uid": str(self._user_id),
                    "title": event.title,
                    "start": event.start,
                    "end": event.end,
                    "all_day": event.all_day,
                    "descr": event.description,
                    "loc": event.location,
                    "rrule": event.recurrence,
                    "flex": event.flexibility.value,
                    "expected": expected_version,
                },
            )
        ).first()

        if row is None:
            # Distingue "não existe" de "mudou": o PlanExecutor reage
            # diferente a cada um (§5.8).
            exists = (
                await self._db.execute(
                    text(
                        "SELECT version FROM core.local_events "
                        "WHERE id = :eid AND user_id = :uid "
                        "AND deleted_at IS NULL"
                    ),
                    {"eid": event.id, "uid": str(self._user_id)},
                )
            ).scalar_one_or_none()

            if exists is None:
                raise EventNotFound(f"evento {event.id} não existe")
            raise ConcurrencyConflict(
                f"evento {event.id} mudou desde a leitura "
                f"(versão {exists}, esperada {expected_version})"
            )

        return self._to_event(row)

    async def delete_event(
        self, calendar_id: str, event_id: str, expected_etag: str | None = None
    ) -> None:
        """Soft delete: permite o undo de 7 dias (§5.8)."""
        expected_version = None
        if expected_etag is not None:
            try:
                expected_version = int(expected_etag)
            except ValueError:
                expected_version = None

        await self._db.execute(
            text(
                """
                UPDATE core.local_events
                SET deleted_at = now(), version = version + 1
                WHERE id = :eid AND user_id = :uid AND deleted_at IS NULL
                  AND (CAST(:expected AS integer) IS NULL
                       OR version = CAST(:expected AS integer))
                """
            ),
            {
                "eid": event_id,
                "uid": str(self._user_id),
                "expected": expected_version,
            },
        )
        # Sem erro se não existia: delete é idempotente por contrato.

    async def restore_from_snapshot(self, snapshot: dict) -> None:
        """Volta um evento ao estado anterior. Usado pelo undo (§5.8)."""
        from datetime import datetime as _dt

        await self._db.execute(
            text(
                """
                UPDATE core.local_events
                SET title = :title, start_at = :start, end_at = :end,
                    description = :descr, location = :loc,
                    flexibility = :flex,
                    version = version + 1, updated_at = now()
                WHERE id = :eid AND user_id = :uid
                """
            ),
            {
                "eid": snapshot["id"],
                "uid": str(self._user_id),
                "title": snapshot["title"],
                "start": _dt.fromisoformat(snapshot["start"]),
                "end": _dt.fromisoformat(snapshot["end"]),
                "descr": snapshot.get("description"),
                "loc": snapshot.get("location"),
                "flex": snapshot.get("flexibility", "fixed"),
            },
        )

    async def restore_event(self, event_id: str) -> None:
        """Desfaz um delete. Usado pelo undo (§5.8), fora do contrato."""
        await self._db.execute(
            text(
                """
                UPDATE core.local_events
                SET deleted_at = NULL, version = version + 1
                WHERE id = :eid AND user_id = :uid
                """
            ),
            {"eid": event_id, "uid": str(self._user_id)},
        )

    # -----------------------------------------------------------
    async def sync(
        self, calendar_id: str, cursor: SyncCursor | None
    ) -> SyncResult:
        """Delta por timestamp de atualização.

        O calendário nativo não precisa de sync para funcionar — os dados
        já estão aqui. Isto existe para que ele passe na suíte de
        contrato como qualquer outro provedor, exercitando o caminho que
        o Google vai usar.
        """
        since = None
        if cursor is not None:
            try:
                since = datetime.fromisoformat(cursor.opaque)
            except ValueError:
                return SyncResult(full_resync_required=True)

        rows = await self._db.execute(
            text(
                """
                SELECT id, local_calendar_id, title, start_at, end_at,
                       all_day, description, location, recurrence,
                       flexibility, created_by_age, version
                FROM core.local_events
                WHERE user_id = :uid AND local_calendar_id = :cal
                  AND deleted_at IS NULL
                  AND (CAST(:since AS timestamptz) IS NULL
                       OR updated_at > CAST(:since AS timestamptz))
                ORDER BY updated_at
                """
            ),
            {"uid": str(self._user_id), "cal": calendar_id, "since": since},
        )
        changed = tuple(self._to_event(r) for r in rows.all())

        deleted: tuple[str, ...] = ()
        if since is not None:
            gone = await self._db.execute(
                text(
                    """
                    SELECT id FROM core.local_events
                    WHERE user_id = :uid AND local_calendar_id = :cal
                      AND deleted_at IS NOT NULL
                      AND deleted_at > CAST(:since AS timestamptz)
                    """
                ),
                {"uid": str(self._user_id), "cal": calendar_id, "since": since},
            )
            deleted = tuple(str(r[0]) for r in gone.all())

        return SyncResult(
            changed=changed,
            deleted_ids=deleted,
            next_cursor=SyncCursor(
                opaque=datetime.now(UTC).isoformat(),
                issued_at=datetime.now(UTC),
            ),
        )

    # -----------------------------------------------------------
    @staticmethod
    def _to_event(row) -> Event:
        return Event(
            id=str(row[0]),
            calendar_id=str(row[1]),
            title=row[2],
            start=row[3],
            end=row[4],
            all_day=row[5],
            description=row[6],
            location=row[7],
            recurrence=row[8],
            flexibility=Flexibility(row[9]),
            source="age" if row[10] else None,
            etag=str(row[11]),
        )
