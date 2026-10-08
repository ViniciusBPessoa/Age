"""Contratos que atravessam a fronteira entre core-api e ai-worker.

Este pacote é a ÚNICA dependência compartilhada entre os dois serviços
(ARQUITETURA.md §2.5). Ele é intencionalmente pequeno: se começar a
crescer, é sinal de que a linha de corte entre os serviços está errada.

Regra: nada aqui importa infraestrutura. Só Pydantic e stdlib.
"""

from age_contracts.events import (
    DoneEvent,
    ErrorEvent,
    PlanEvent,
    StreamEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
    TurnStatus,
    UsageEvent,
    parse_stream_event,
)
from age_contracts.jobs import (
    JobName,
    SyncCalendarPayload,
    TitleConversationPayload,
    TurnPayload,
)
from age_contracts.version import CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "DoneEvent",
    "ErrorEvent",
    "JobName",
    "PlanEvent",
    "StreamEvent",
    "SyncCalendarPayload",
    "TitleConversationPayload",
    "TokenEvent",
    "ToolEndEvent",
    "ToolStartEvent",
    "TurnPayload",
    "TurnStatus",
    "UsageEvent",
    "parse_stream_event",
]
