"""Suíte de contrato de `LLMProvider`.

ARQUITETURA.md §10.1. A mesma suíte roda contra todo adaptador. Um
adaptador novo só é aceito se passar aqui — é assim que "pode trocar o
provedor" deixa de ser promessa e passa a ser fato verificado.

No M1 só o Mock roda offline. DeepSeek e OpenAI exigem chave real e são
marcados para rodar sob demanda (`-m live`), sem bloquear o CI.
"""

from __future__ import annotations

import pytest

from app.adapters.llm import deepseek, mock, openai  # noqa: F401  (registra)
from app.adapters.llm.registry import available_providers, build_llm, is_registered
from app.ports.llm import (
    AuthenticationError,
    Completion,
    ContextTooLong,
    LLMProvider,
    MalformedToolCall,
    Message,
    ProviderTimeout,
    RateLimitError,
    ToolSpec,
)

_TOOL = ToolSpec(
    name="get_events",
    description="Lê compromissos de um período",
    parameters={
        "type": "object",
        "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        "required": ["start"],
    },
)


def offline_providers() -> list[LLMProvider]:
    """Provedores que rodam sem rede nem credencial."""
    return [build_llm("mock", api_key="x", model="mock-chat")]


# ---------------------------------------------------------------
# Registry
# ---------------------------------------------------------------
def test_expected_providers_are_registered() -> None:
    for key in ("deepseek", "openai", "mock"):
        assert is_registered(key), f"provedor '{key}' não registrado"


def test_catalog_shape() -> None:
    """O catálogo alimenta o onboarding e o seletor (§8.1)."""
    for entry in available_providers():
        assert entry["key"] and entry["name"]
        assert entry["default_model"] in entry["models"]


def test_unknown_provider_raises() -> None:
    from app.ports.llm import NotSupported

    with pytest.raises(NotSupported):
        build_llm("provedor-que-nao-existe", api_key="x")


# ---------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------
@pytest.mark.parametrize("provider", offline_providers(), ids=lambda p: p.key)
class TestLLMProviderContract:
    async def test_simple_completion(self, provider: LLMProvider) -> None:
        result = await provider.complete([Message(role="user", content="olá")])
        assert isinstance(result, Completion)
        assert result.message.role == "assistant"
        assert result.finish_reason in {"stop", "tool_calls", "length"}
        await provider.aclose()

    async def test_streaming_concatenates_to_text(
        self, provider: LLMProvider
    ) -> None:
        parts: list[str] = []
        finished = False
        async for chunk in provider.stream([Message(role="user", content="oi")]):
            if chunk.text_delta:
                parts.append(chunk.text_delta)
            if chunk.done:
                finished = True
        assert finished, "stream deve terminar com chunk.done"
        assert "".join(parts).strip(), "stream não produziu texto"
        await provider.aclose()

    async def test_tool_call_arguments_are_always_dict(
        self, provider: LLMProvider
    ) -> None:
        """Seja string JSON (OpenAI) ou objeto (Gemini), chega dict.

        É a assimetria de §3.1.1 que a porta existe para esconder.
        """
        if not provider.capabilities.supports_tools:
            pytest.skip("provedor sem tools")

        result = await provider.complete(
            [Message(role="user", content="[tool] lê minha agenda")],
            tools=[_TOOL],
        )
        if result.message.tool_calls:
            for call in result.message.tool_calls:
                assert isinstance(call.arguments, dict)
                assert call.id, "tool call precisa de id no canônico"
        await provider.aclose()

    async def test_usage_is_reported(self, provider: LLMProvider) -> None:
        result = await provider.complete([Message(role="user", content="oi")])
        assert result.usage.prompt_tokens >= 0
        assert result.usage.completion_tokens >= 0
        await provider.aclose()

    async def test_capabilities_are_coherent(self, provider: LLMProvider) -> None:
        caps = provider.capabilities
        assert caps.context_window > 0
        assert caps.max_output_tokens > 0
        assert caps.max_output_tokens <= caps.context_window
        # Parallel só faz sentido se houver tools.
        if caps.supports_parallel_tool_calls:
            assert caps.supports_tools
        await provider.aclose()

    async def test_cost_estimate_or_none(self, provider: LLMProvider) -> None:
        """Modelo local não tem preço, e isso é None — não zero fingido."""
        from app.ports.llm import Usage

        caps = provider.capabilities
        cost = caps.estimate_cost_usd(Usage(1_000_000, 1_000_000))
        if caps.cost_per_1m_input is None:
            assert cost is None
        else:
            assert cost is not None and cost >= 0
        await provider.aclose()

    async def test_health_check(self, provider: LLMProvider) -> None:
        assert await provider.health_check() is True
        await provider.aclose()

    async def test_aclose_is_idempotent(self, provider: LLMProvider) -> None:
        await provider.aclose()
        await provider.aclose()


# ---------------------------------------------------------------
# Erros tipados — o Mock existe para cobrir estes caminhos
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    "model,expected",
    [
        ("mock-fail-auth", AuthenticationError),
        ("mock-fail-timeout", ProviderTimeout),
        ("mock-fail-ratelimit", RateLimitError),
        ("mock-fail-context", ContextTooLong),
        ("mock-bad-tool-args", MalformedToolCall),
    ],
)
async def test_errors_are_typed(model: str, expected: type[Exception]) -> None:
    """Erro tipado é o que permite ao orquestrador reagir certo.

    Sem isto, toda falha viraria "algo deu errado" e o usuário não
    saberia se deve trocar a chave, esperar ou começar outra conversa
    (§5.9.13).
    """
    provider = build_llm("mock", api_key="x", model=model)
    with pytest.raises(expected):
        await provider.complete([Message(role="user", content="oi")])
    await provider.aclose()


async def test_error_codes_are_stable() -> None:
    """O front mapeia `code` para mensagem localizada (§7.0)."""
    assert AuthenticationError("x").code == "llm_auth_failed"
    assert RateLimitError("x").code == "llm_rate_limited"
    assert ProviderTimeout("x").code == "llm_timeout"
    assert ContextTooLong("x").code == "llm_context_too_long"
    assert MalformedToolCall("x").code == "llm_malformed_tool_call"


async def test_retryable_flags() -> None:
    """Quais falhas valem nova tentativa, e quais não."""
    assert RateLimitError("x").retryable is True
    assert ProviderTimeout("x").retryable is True
    # Chave errada não melhora com retry.
    assert AuthenticationError("x").retryable is False
    assert ContextTooLong("x").retryable is False


async def test_provider_without_tools_degrades_declared() -> None:
    """Declarar é prometer: sem tools, o campo diz isso (§3.1.2)."""
    provider = build_llm("mock", api_key="x", model="mock-no-tools")
    caps = provider.capabilities
    assert caps.supports_tools is False
    assert caps.supports_parallel_tool_calls is False
    assert caps.supports_system_role is False
    await provider.aclose()
