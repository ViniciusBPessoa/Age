"""Porta `LLMProvider` — o contrato único de todo provedor de modelo.

ARQUITETURA.md §3.1. Os tipos aqui são PRÓPRIOS do Age, não os de
nenhum provedor. Isso parece excesso de cerimônia até o segundo
adaptador entrar: DeepSeek é compatível com o formato da OpenAI, e
adotá-lo como "neutro" pareceria conveniente — até o Gemini chegar, que
não tem id de tool call, usa `role: "model"` e manda o system prompt num
campo separado (§3.1.1).

Nada neste arquivo importa SDK, httpx ou qualquer infraestrutura. É a
invariante verificada em §10.2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------
# Mensagens e ferramentas — formato canônico
# ---------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    """Pedido de ferramenta, já normalizado.

    `arguments` chega SEMPRE como dict, mesmo em provedores que mandam
    string JSON (OpenAI, DeepSeek). A tradução é do adaptador.

    `id` existe sempre no canônico. O Gemini não tem ids, então o
    adaptador gera um sintético determinístico — sem isso, o histórico
    não consegue casar resposta com chamada quando o modelo pede duas
    ferramentas no mesmo turno (§3.1.1).
    """

    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class Completion:
    message: Message
    usage: Usage
    finish_reason: Literal["stop", "tool_calls", "length", "filter", "error"]


@dataclass(frozen=True)
class Chunk:
    """Fragmento de streaming.

    Texto e tool call são separados porque o orquestrador trata cada um
    de forma diferente: texto vai direto ao usuário, tool call é
    acumulado até o `finish_reason` (§5.1).
    """

    text_delta: str | None = None
    tool_call_delta: dict | None = None
    finish_reason: str | None = None
    usage: Usage | None = None
    done: bool = False


# ---------------------------------------------------------------
# Capacidades
# ---------------------------------------------------------------
@dataclass(frozen=True)
class ModelCapabilities:
    """O que este provedor/modelo sabe fazer (§3.1.2).

    Consultado por atributo, nunca por `if provider_key == "gemini"`. O
    nome do provedor não aparece em nenhum ramo de decisão fora de
    `adapters/`.
    """

    supports_tools: bool
    supports_parallel_tool_calls: bool
    supports_streaming: bool
    supports_system_role: bool
    supports_json_mode: bool
    context_window: int
    max_output_tokens: int
    max_tools: int | None = None
    cost_per_1m_input: float | None = None
    cost_per_1m_output: float | None = None

    def estimate_cost_usd(self, usage: Usage) -> float | None:
        """Custo estimado. None quando o modelo é local ou desconhecido."""
        if self.cost_per_1m_input is None or self.cost_per_1m_output is None:
            return None
        return (
            usage.prompt_tokens / 1_000_000 * self.cost_per_1m_input
            + usage.completion_tokens / 1_000_000 * self.cost_per_1m_output
        )


# ---------------------------------------------------------------
# Erros tipados
# ---------------------------------------------------------------
class LLMError(Exception):
    """Base. `code` é o que o front mapeia para mensagem (§7.0)."""

    code = "llm_error"
    retryable = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider


class AuthenticationError(LLMError):
    code = "llm_auth_failed"


class RateLimitError(LLMError):
    code = "llm_rate_limited"
    retryable = True


class ProviderTimeout(LLMError):
    code = "llm_timeout"
    retryable = True


class ProviderUnavailable(LLMError):
    code = "llm_unavailable"
    retryable = True


class ContextTooLong(LLMError):
    code = "llm_context_too_long"


class MalformedToolCall(LLMError):
    """JSON de argumentos inválido ou truncado.

    Acontece de verdade com `finish_reason: "length"`. O orquestrador
    devolve o erro ao modelo para ele tentar de novo — uma vez só
    (§3.1.1).
    """

    code = "llm_malformed_tool_call"
    retryable = True


class NotSupported(LLMError):
    code = "llm_not_supported"


# ---------------------------------------------------------------
# A porta
# ---------------------------------------------------------------
class LLMProvider(ABC):
    """Contrato único. Um adaptador por provedor."""

    key: str
    display_name: str
    known_models: tuple[str, ...] = ()
    default_model: str = ""

    @property
    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """Depende do MODELO escolhido, não só do provedor.

        gpt-4o tem tools; um modelo pequeno no Ollama não.
        """

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Completion: ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]: ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Valida a credencial sem gastar tokens relevantes."""

    @abstractmethod
    async def aclose(self) -> None:
        """Libera a conexão HTTP."""
