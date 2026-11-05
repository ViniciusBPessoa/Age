"""Porta `CalendarProvider` — o contrato de todo provedor de agenda.

ARQUITETURA.md §3.2. Os tipos aqui são próprios do Age, e a porta foi
desenhada a partir do provedor MAIS RICO, degradando nos pobres (§3.3.1).
Partir do Google Calendar faria campos essenciais nem existirem.

Duas decisões que merecem destaque:

1. **O cursor de sync é opaco.** O núcleo nunca interpreta o que há
   dentro: pode ser `syncToken` do Google, CTag do CalDAV, deltaLink do
   Outlook ou um timestamp do calendário nativo. Na v0.1 do documento
   esses nomes vazavam para colunas do banco — o jeito mais caro de
   errar, porque campo de tabela é difícil de mudar depois.

2. **Capacidade é declarada, não presumida.** Pedir recorrência a um
   provedor que não suporta não dá erro silencioso: ou o `PlanExecutor`
   expande a série em eventos individuais e avisa o usuário, ou levanta
   `NotSupported`.

Nada neste arquivo importa SDK, httpx ou SQLAlchemy — invariante de
§10.2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# ---------------------------------------------------------------
# Entidades
# ---------------------------------------------------------------
EVENT_COLORS: tuple[str, ...] = (
    "tomate",
    "tangerina",
    "banana",
    "salvia",
    "manjericao",
    "pavao",
    "mirtilo",
    "lavanda",
    "uva",
    "flamingo",
    "grafite",
)
"""As cores de categoria de um evento. Os nomes seguem as onze cores de
evento que a pessoa já conhece do Google Agenda, em português."""


@dataclass(frozen=True)
class CalendarRef:
    """Um calendário dentro de uma conexão."""

    id: str
    name: str
    timezone: str
    is_primary: bool = False
    writable: bool = True
    color: str | None = None


class Flexibility(StrEnum):
    """Quanto um evento pode ser movido pelo assistente (§5.6).

    O viés é sempre para mais rígido: na dúvida, `FIXED`. Mover a
    consulta médica de alguém por heurística errada é o tipo de erro
    que destrói a confiança no produto de uma vez.
    """

    FIXED = "fixed"
    SEMI_FLEXIBLE = "semi_flexible"
    FLEXIBLE = "flexible"


@dataclass(frozen=True)
class Event:
    """Um compromisso com hora marcada.

    `start`/`end` são SEMPRE timezone-aware. Tempo sem fuso é a origem
    da maioria dos bugs num produto de agenda, então a porta não aceita
    naive datetime — a validação está em `__post_init__`.
    """

    id: str | None
    calendar_id: str
    title: str
    start: datetime
    end: datetime
    description: str | None = None
    location: str | None = None
    all_day: bool = False
    recurrence: str | None = None  # RRULE (RFC 5545)
    recurring_event_id: str | None = None  # instância de uma série
    attendees: tuple[str, ...] = ()
    # "age" quando criado por nós: permite saber o que podemos mexer.
    source: str | None = None
    etag: str | None = None
    # Metadado local, não vem do provedor (§6.3).
    flexibility: Flexibility = Flexibility.FIXED
    # Ocorrência de uma série recorrente do Age (§5.9.4). Também é
    # metadado local: o provedor externo tem a própria noção de
    # recorrência (`recurrence`), que é outra coisa — ela descreve a
    # repetição no calendário, esta diz a qual rotina do Age o evento
    # pertence, para a aderência saber onde registrar.
    routine_id: str | None = None
    # Cor de categoria, escolhida pela pessoa na agenda (§8.1). Um nome
    # da paleta fixa (`EVENT_COLORS`), nunca hex livre: o tema claro e o
    # escuro pintam o mesmo nome com tons diferentes, e hex livre ficaria
    # ilegível num dos dois. Metadado local, como `flexibility`.
    color: str | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError(
                f"Event '{self.title}': start e end precisam de timezone. "
                "Datetime naive é a causa raiz de quase todo bug de agenda."
            )
        if self.end < self.start:
            raise ValueError(
                f"Event '{self.title}': end ({self.end}) antes de start "
                f"({self.start})"
            )

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def has_attendees(self) -> bool:
        return len(self.attendees) > 0


@dataclass(frozen=True)
class BusyInterval:
    """Intervalo ocupado, sem identidade.

    É o que o `Scheduler` consome: ele não precisa saber o que ocupa o
    horário, só que está ocupado. No modo privado (§9.3) é tudo que
    sai do sistema.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("BusyInterval exige datetime com timezone")

    def overlaps(self, other: BusyInterval) -> bool:
        return self.start < other.end and other.start < self.end

    def touches(self, other: BusyInterval) -> bool:
        """Sobrepõe OU encosta — usado na fusão de intervalos."""
        return self.start <= other.end and other.start <= self.end


# ---------------------------------------------------------------
# Sincronização como capacidade negociada (§3.2.2)
# ---------------------------------------------------------------
class SyncCapability(StrEnum):
    INCREMENTAL = "incremental"  # cursor de delta
    PUSH = "push"  # incremental + webhook
    POLL_ONLY = "poll_only"  # só refetch de janela


@dataclass(frozen=True)
class SyncCursor:
    """Estado de sincronização. O núcleo NUNCA interpreta `opaque`."""

    opaque: str
    issued_at: datetime


@dataclass(frozen=True)
class SyncResult:
    changed: tuple[Event, ...] = ()
    deleted_ids: tuple[str, ...] = ()
    next_cursor: SyncCursor | None = None
    # Cursor expirado ou inválido. É o caminho que PRECISA estar
    # testado: acontece depois de qualquer indisponibilidade longa.
    full_resync_required: bool = False


@dataclass(frozen=True)
class Subscription:
    """Assinatura de notificação push."""

    opaque: str
    expires_at: datetime


# ---------------------------------------------------------------
# Capacidades
# ---------------------------------------------------------------
@dataclass(frozen=True)
class CalendarCapabilities:
    supports_recurrence: bool
    supports_attendees: bool
    supports_etag_concurrency: bool
    supports_multiple_calendars: bool
    supports_colors: bool
    sync: SyncCapability
    max_window_days: int | None = None


# ---------------------------------------------------------------
# Erros
# ---------------------------------------------------------------
class CalendarError(Exception):
    code = "calendar_error"
    retryable = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider


class NotSupported(CalendarError):
    code = "calendar_not_supported"


class AuthenticationError(CalendarError):
    code = "calendar_auth_failed"


class RateLimitError(CalendarError):
    code = "calendar_rate_limited"
    retryable = True


class ProviderUnavailable(CalendarError):
    code = "calendar_unavailable"
    retryable = True


class EventNotFound(CalendarError):
    code = "event_not_found"


class ConcurrencyConflict(CalendarError):
    """O evento mudou desde a leitura (etag divergente).

    Importa porque o `PlanExecutor` revalida antes de aplicar (§5.8):
    aprovar um plano e descobrir que a agenda mudou é melhor que
    sobrescrever a mudança de alguém em silêncio.
    """

    code = "concurrency_conflict"


# ---------------------------------------------------------------
# A porta
# ---------------------------------------------------------------
class CalendarProvider(ABC):
    key: str
    display_name: str

    @property
    @abstractmethod
    def capabilities(self) -> CalendarCapabilities: ...

    @abstractmethod
    async def list_calendars(self) -> list[CalendarRef]: ...

    @abstractmethod
    async def list_events(
        self,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[Event]:
        """Eventos de uma janela.

        Recebe LISTA de calendários porque o modo híbrido (§3.2.1) lê de
        vários ao mesmo tempo. Com um provedor só isso pareceria excesso
        de generalidade; com dois é requisito.
        """

    @abstractmethod
    async def free_busy(
        self,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[BusyInterval]:
        """Só os intervalos ocupados, sem títulos.

        Caminho separado de `list_events` porque é o que o modo privado
        usa (§9.3), e porque alguns provedores têm endpoint dedicado
        bem mais barato.
        """

    @abstractmethod
    async def create_event(self, event: Event) -> Event: ...

    @abstractmethod
    async def update_event(
        self, event: Event, expected_etag: str | None = None
    ) -> Event: ...

    @abstractmethod
    async def delete_event(
        self, calendar_id: str, event_id: str, expected_etag: str | None = None
    ) -> None:
        """Remove. Idempotente: apagar o que não existe não é erro."""

    @abstractmethod
    async def sync(
        self, calendar_id: str, cursor: SyncCursor | None
    ) -> SyncResult: ...

    async def watch(self, calendar_id: str, webhook_url: str) -> Subscription:
        """Assina push. Só quem suporta sobrescreve."""
        raise NotSupported(f"{self.key} não suporta notificação push")

    async def unwatch(self, subscription: Subscription) -> None:
        raise NotSupported(f"{self.key} não suporta notificação push")

    async def aclose(self) -> None:
        """Libera conexões. Default é no-op."""
        return None
