"""Adaptador Mock — roteiros determinísticos, sem rede.

Existe por três razões concretas:

1. **A suíte inteira roda sem gastar token** e sem chave de API.
2. **Testar caminhos difíceis de provocar em provedor real:** tool call
   malformado, timeout, rate limit, contexto estourado.
3. **Modo demo:** dá para subir o produto e ver a interface funcionando
   antes de configurar credencial nenhuma.

É um provedor de primeira classe na suíte de contrato (§10.1): se ele
quebra, o contrato mudou.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.adapters.llm.registry import register_llm
from app.ports.llm import (
    AuthenticationError,
    Chunk,
    Completion,
    ContextTooLong,
    LLMProvider,
    MalformedToolCall,
    Message,
    ModelCapabilities,
    ProviderTimeout,
    RateLimitError,
    ToolCall,
    ToolSpec,
    Usage,
)

_CAPS = ModelCapabilities(
    supports_tools=True,
    supports_parallel_tool_calls=True,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=True,
    context_window=32_000,
    max_output_tokens=4_096,
    cost_per_1m_input=0.0,
    cost_per_1m_output=0.0,
)

# Sem tools: exercita o caminho de degradação de §3.1.2.
_CAPS_NO_TOOLS = ModelCapabilities(
    supports_tools=False,
    supports_parallel_tool_calls=False,
    supports_streaming=True,
    supports_system_role=False,
    supports_json_mode=False,
    context_window=4_000,
    max_output_tokens=512,
    cost_per_1m_input=None,
    cost_per_1m_output=None,
)


@register_llm
class MockProvider(LLMProvider):
    key = "mock"
    display_name = "Mock (testes)"
    known_models = (
        "mock-chat",
        "mock-no-tools",
        "mock-fail-auth",
        "mock-fail-timeout",
        "mock-fail-ratelimit",
        "mock-fail-context",
        "mock-bad-tool-args",
    )
    default_model = "mock-chat"

    def __init__(
        self,
        *,
        api_key: str = "mock",
        model: str = "mock-chat",
        base_url: str | None = None,
    ) -> None:
        self._model = model or self.default_model
        self._api_key = api_key

    @property
    def capabilities(self) -> ModelCapabilities:
        if self._model == "mock-no-tools":
            return _CAPS_NO_TOOLS
        return _CAPS

    def _maybe_fail(self) -> None:
        """Cenários de falha, selecionados pelo nome do modelo."""
        match self._model:
            case "mock-fail-auth":
                raise AuthenticationError("chave inválida (mock)", provider=self.key)
            case "mock-fail-timeout":
                raise ProviderTimeout("tempo esgotado (mock)", provider=self.key)
            case "mock-fail-ratelimit":
                raise RateLimitError("limite atingido (mock)", provider=self.key)
            case "mock-fail-context":
                raise ContextTooLong("contexto estourado (mock)", provider=self.key)
            case "mock-bad-tool-args":
                raise MalformedToolCall(
                    "argumentos inválidos para 'get_events' (mock)",
                    provider=self.key,
                )

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Completion:
        self._maybe_fail()
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )

        # Com tools disponíveis e marcador na mensagem, devolve tool call.
        # Permite testar o laço do orquestrador de forma determinística.
        if tools and self.capabilities.supports_tools and "[tool]" in (last_user or ""):
            return Completion(
                message=Message(
                    role="assistant",
                    content=None,
                    tool_calls=(
                        ToolCall(
                            id="call_mock_1",
                            name=tools[0].name,
                            arguments={},
                        ),
                    ),
                ),
                usage=Usage(prompt_tokens=20, completion_tokens=5),
                finish_reason="tool_calls",
            )

        return Completion(
            message=Message(
                role="assistant", content=f"[mock] recebi: {last_user or '(vazio)'}"
            ),
            usage=Usage(prompt_tokens=20, completion_tokens=12),
            finish_reason="stop",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        self._maybe_fail()
        completion = await self.complete(
            messages, tools, temperature, max_tokens
        )

        if completion.message.tool_calls:
            for tc in completion.message.tool_calls:
                yield Chunk(
                    tool_call_delta={
                        "index": 0,
                        "id": tc.id,
                        "function": {"name": tc.name, "arguments": "{}"},
                    }
                )
            yield Chunk(
                finish_reason="tool_calls", usage=completion.usage, done=True
            )
            return

        for word in (completion.message.content or "").split():
            yield Chunk(text_delta=word + " ")
            await asyncio.sleep(0.01)
        yield Chunk(finish_reason="stop", usage=completion.usage, done=True)

    async def health_check(self) -> bool:
        self._maybe_fail()
        return True

    async def aclose(self) -> None:
        return None
