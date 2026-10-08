"""Troca de provedor no meio da conversa — o requisito de §3.1.3.

"Quero começar usando o DeepSeek, mas depois ir para um ChatGPT ou
Gemini só para testar."

Fazer isso de forma ingênua **falha com erro de API**, não com resposta
pior: o histórico persistido tem `tool_call_id` em formato do provedor
antigo, e os papéis não batem. Estes testes cobrem toda permutação de
capacidades para garantir que não aconteça.
"""

from __future__ import annotations

import itertools

import pytest

from app.domain.history import normalize_for
from app.ports.llm import Message, ModelCapabilities, ToolCall

# Perfis que representam os provedores reais, por CAPACIDADE — não por
# nome. É o que mantém o teste válido quando um provedor novo entra.
PROFILES: dict[str, ModelCapabilities] = {
    "deepseek-chat": ModelCapabilities(
        supports_tools=True,
        supports_parallel_tool_calls=True,
        supports_streaming=True,
        supports_system_role=True,
        supports_json_mode=True,
        context_window=64_000,
        max_output_tokens=8_192,
    ),
    "deepseek-reasoner": ModelCapabilities(
        supports_tools=False,
        supports_parallel_tool_calls=False,
        supports_streaming=True,
        supports_system_role=True,
        supports_json_mode=False,
        context_window=64_000,
        max_output_tokens=8_192,
    ),
    "openai-gpt4o": ModelCapabilities(
        supports_tools=True,
        supports_parallel_tool_calls=True,
        supports_streaming=True,
        supports_system_role=True,
        supports_json_mode=True,
        context_window=128_000,
        max_output_tokens=16_384,
    ),
    # Gemini: sem role `system` separado no formato de mensagens.
    "gemini-like": ModelCapabilities(
        supports_tools=True,
        supports_parallel_tool_calls=False,
        supports_streaming=True,
        supports_system_role=False,
        supports_json_mode=True,
        context_window=32_000,
        max_output_tokens=8_192,
    ),
    # Ollama pequeno: nem tools, nem system, janela apertada.
    "ollama-small": ModelCapabilities(
        supports_tools=False,
        supports_parallel_tool_calls=False,
        supports_streaming=True,
        supports_system_role=False,
        supports_json_mode=False,
        context_window=4_000,
        max_output_tokens=512,
    ),
}


def realistic_history() -> list[Message]:
    """Conversa com system, texto, tool calls paralelos e resultados."""
    return [
        Message(role="system", content="Você é o Age, assistente de agenda."),
        Message(role="user", content="preciso estudar álgebra linear"),
        Message(
            role="assistant",
            content="Vou olhar sua agenda.",
            tool_calls=(
                ToolCall(id="call_1", name="get_events", arguments={"days": 14}),
                ToolCall(
                    id="call_2",
                    name="find_free_slots",
                    arguments={"duration_min": 80},
                ),
            ),
        ),
        Message(role="tool", content='{"count": 12}', tool_call_id="call_1"),
        Message(role="tool", content='{"slots": 6}', tool_call_id="call_2"),
        Message(role="assistant", content="Achei 6 janelas livres."),
        Message(role="user", content="e se a prova for dia 20?"),
    ]


@pytest.mark.parametrize(
    "origin,destination",
    list(itertools.permutations(PROFILES.keys(), 2)),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_history_survives_provider_switch(origin: str, destination: str) -> None:
    """Conversa num provedor, troca para outro, histórico continua válido.

    Verifica as três invariantes que, violadas, causam erro de API:
      - nenhum par tool_call/tool_result órfão
      - nada que o destino não suporta
      - o contexto anterior continua acessível
    """
    # Histórico canônico (persistido), normalizado para a origem.
    canonical = realistic_history()
    _ = normalize_for(canonical, PROFILES[origin])

    # Troca: o canônico é renormalizado para o destino, não o da origem.
    # Isso é o ponto — o banco guarda o canônico (§3.1.3).
    result = normalize_for(canonical, PROFILES[destination])
    caps = PROFILES[destination]

    assert result, f"{origin} → {destination} devolveu vazio"

    # 1. Nenhum resultado de ferramenta sem a chamada correspondente.
    requested = {tc.id for m in result for tc in m.tool_calls}
    answered = {m.tool_call_id for m in result if m.role == "tool"}
    assert answered <= requested, (
        f"{origin} → {destination}: resultado órfão {answered - requested}"
    )

    # 2. Nada que o destino não suporta.
    if not caps.supports_tools:
        assert all(not m.tool_calls for m in result)
        assert all(m.role != "tool" for m in result)
    if not caps.supports_system_role:
        assert all(m.role != "system" for m in result)
    if not caps.supports_parallel_tool_calls:
        assert all(len(m.tool_calls) <= 1 for m in result)

    # 3. A última pergunta do usuário sobrevive sempre — sem ela o
    # modelo não tem o que responder.
    text = " ".join(m.content or "" for m in result)
    assert "dia 20" in text, f"{origin} → {destination} perdeu a última mensagem"


@pytest.mark.parametrize("destination", list(PROFILES.keys()))
def test_switch_preserves_earlier_reasoning(destination: str) -> None:
    """O que foi descoberto antes não se perde na troca.

    Num destino sem tools, a troca vira narração em texto em vez de
    desaparecer — é o que permite o modelo novo continuar de onde o
    anterior parou.
    """
    result = normalize_for(realistic_history(), PROFILES[destination])
    text = " ".join(m.content or "" for m in result)

    caps = PROFILES[destination]
    if not caps.supports_tools and caps.context_window > 8_000:
        # Sem tools e com janela folgada: a narração deve estar lá.
        assert "get_events" in text or "find_free_slots" in text


def test_canonical_history_has_no_provider_specific_fields() -> None:
    """O formato canônico não é o de ninguém (§3.1.1).

    Se o canônico tivesse campo de provedor, a porta estaria vazando — e
    o segundo adaptador exigiria mudar a porta inteira.
    """
    from dataclasses import fields

    message_fields = {f.name for f in fields(Message)}
    assert message_fields == {"role", "content", "tool_calls", "tool_call_id"}

    call_fields = {f.name for f in fields(ToolCall)}
    assert call_fields == {"id", "name", "arguments"}

    # Nada de `function`, `functionCall`, `parts`, `@odata` etc.
    forbidden = {"function", "functionCall", "parts", "candidates", "input_schema"}
    assert not (message_fields | call_fields) & forbidden
