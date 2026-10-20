"""Eventos que o ai-worker publica no Redis Stream e o core-api repassa
como SSE (ARQUITETURA.md §7.1).

O navegador nunca fala com o worker: o worker publica, o core-api assina e
relaya. Por isso estes tipos são contrato — se divergirem, o stream chega
como lixo no browser.
"""

from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class TurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _BaseEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TokenEvent(_BaseEvent):
    """Pedaço de texto gerado pelo modelo."""

    type: Literal["token"] = "token"
    delta: str


class ToolStartEvent(_BaseEvent):
    """O modelo pediu uma ferramenta.

    Não é enfeite: com o laço em outro processo, é o único sinal de que o
    worker está vivo durante as iterações silenciosas (§5.1).
    """

    type: Literal["tool_start"] = "tool_start"
    name: str


class ToolEndEvent(_BaseEvent):
    type: Literal["tool_end"] = "tool_end"
    name: str
    ok: bool = True
    summary: str | None = None


class PlanEvent(_BaseEvent):
    """Um plano foi proposto e espera aprovação (§5.7)."""

    type: Literal["plan"] = "plan"
    plan_id: str
    item_count: int


class UsageEvent(_BaseEvent):
    """Quanto o turno consumiu, ao fim da geração.

    Publicado DEPOIS da persistência e ANTES do `done`, então quando ele
    chega o custo já está em `ai.turn_usage` — o número que o front
    mostra ao vivo e o que uma releitura de `/messages` devolve são o
    mesmo.

    `cost_usd` é `None` quando o modelo não tem preço conhecido (local,
    ou provedor sem tabela). Nunca `0.0`: zero afirmaria gratuidade. O
    default existe para que um worker novo publicando para um core-api
    antigo, e vice-versa, não quebre no parse.
    """

    type: Literal["usage"] = "usage"
    prompt_tokens: int
    completion_tokens: int
    model: str
    provider_key: str
    cost_usd: float | None = None
    cached_prompt_tokens: int | None = None


class DoneEvent(_BaseEvent):
    type: Literal["done"] = "done"
    message_id: str
    status: TurnStatus = TurnStatus.DONE


class ErrorEvent(_BaseEvent):
    """Falha que o usuário precisa ver.

    `code` é legível por máquina para o front mapear a mensagem (§7.0);
    `message` é fallback em pt-BR.
    """

    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False


StreamEvent = Annotated[
    Union[
        TokenEvent,
        ToolStartEvent,
        ToolEndEvent,
        PlanEvent,
        UsageEvent,
        DoneEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]

_adapter: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)


def parse_stream_event(raw: dict[str, object]) -> StreamEvent:
    """Converte um dict do Redis Stream no evento tipado correspondente."""
    return _adapter.validate_python(raw)
