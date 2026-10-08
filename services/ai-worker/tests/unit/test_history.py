"""Testes de `normalize_for` — a função que faz a troca de provedor
funcionar (ARQUITETURA.md §3.1.3).

Função pura, então os testes não precisam de rede nem banco. É o que
permite cobrir as combinações todas de forma barata.
"""

from __future__ import annotations

import pytest

from app.domain.history import estimate_tokens, normalize_for
from app.ports.llm import Message, ModelCapabilities, ToolCall


def caps(**overrides) -> ModelCapabilities:
    base = {
        "supports_tools": True,
        "supports_parallel_tool_calls": True,
        "supports_streaming": True,
        "supports_system_role": True,
        "supports_json_mode": True,
        "context_window": 32_000,
        "max_output_tokens": 2_048,
    }
    return ModelCapabilities(**{**base, **overrides})


def _exchange() -> list[Message]:
    """Histórico com uma troca de ferramenta completa."""
    return [
        Message(role="system", content="Você é o Age."),
        Message(role="user", content="o que tenho amanhã?"),
        Message(
            role="assistant",
            tool_calls=(
                ToolCall(id="c1", name="get_events", arguments={"day": "amanhã"}),
            ),
        ),
        Message(role="tool", content='{"events": 3}', tool_call_id="c1"),
        Message(role="assistant", content="Você tem 3 compromissos."),
    ]


# ---------------------------------------------------------------
# 1. Pares órfãos
# ---------------------------------------------------------------
def test_removes_tool_call_without_result() -> None:
    """Chamada sem resposta é rejeitada por todo provedor.

    Acontece de verdade quando um turno é cancelado no meio (§7.1).
    """
    messages = [
        Message(role="user", content="oi"),
        Message(
            role="assistant",
            tool_calls=(ToolCall(id="orfao", name="get_events", arguments={}),),
        ),
    ]
    result = normalize_for(messages, caps())
    assert all(not m.tool_calls for m in result)


def test_removes_tool_result_without_call() -> None:
    messages = [
        Message(role="user", content="oi"),
        Message(role="tool", content="{}", tool_call_id="nunca_pedido"),
    ]
    result = normalize_for(messages, caps())
    assert all(m.role != "tool" for m in result)


def test_keeps_text_when_dropping_unanswered_call() -> None:
    """Se a mensagem tinha texto junto da chamada, o texto é preservado."""
    messages = [
        Message(role="user", content="oi"),
        Message(
            role="assistant",
            content="Vou verificar.",
            tool_calls=(ToolCall(id="orfao", name="get_events", arguments={}),),
        ),
    ]
    result = normalize_for(messages, caps())
    assert any(m.content == "Vou verificar." for m in result)


# ---------------------------------------------------------------
# 2. Destino sem tools
# ---------------------------------------------------------------
def test_narrates_tool_exchange_for_provider_without_tools() -> None:
    """Preserva o raciocínio em texto em vez de apagá-lo."""
    result = normalize_for(_exchange(), caps(supports_tools=False))

    assert all(m.role != "tool" for m in result)
    assert all(not m.tool_calls for m in result)
    narration = " ".join(m.content or "" for m in result)
    assert "get_events" in narration
    assert "events" in narration  # o resultado também


# ---------------------------------------------------------------
# 3. Destino sem parallel tool calls
# ---------------------------------------------------------------
def test_splits_parallel_calls_into_sequential_turns() -> None:
    messages = [
        Message(role="user", content="e amanhã?"),
        Message(
            role="assistant",
            tool_calls=(
                ToolCall(id="a", name="get_events", arguments={}),
                ToolCall(id="b", name="find_free_slots", arguments={}),
            ),
        ),
        Message(role="tool", content="{}", tool_call_id="a"),
        Message(role="tool", content="{}", tool_call_id="b"),
    ]
    result = normalize_for(messages, caps(supports_parallel_tool_calls=False))

    # Nenhuma mensagem com mais de uma chamada.
    assert all(len(m.tool_calls) <= 1 for m in result)
    # As duas chamadas sobreviveram.
    names = [tc.name for m in result for tc in m.tool_calls]
    assert names == ["get_events", "find_free_slots"]
    # Cada uma seguida do seu resultado.
    assert sum(1 for m in result if m.role == "tool") == 2


def test_tool_result_follows_its_own_call_after_split() -> None:
    messages = [
        Message(
            role="assistant",
            tool_calls=(
                ToolCall(id="a", name="first", arguments={}),
                ToolCall(id="b", name="second", arguments={}),
            ),
        ),
        Message(role="tool", content="ra", tool_call_id="a"),
        Message(role="tool", content="rb", tool_call_id="b"),
    ]
    result = normalize_for(messages, caps(supports_parallel_tool_calls=False))

    sequence = [
        (m.role, m.tool_calls[0].id if m.tool_calls else m.tool_call_id)
        for m in result
    ]
    assert sequence == [
        ("assistant", "a"),
        ("tool", "a"),
        ("assistant", "b"),
        ("tool", "b"),
    ]


# ---------------------------------------------------------------
# 4. Destino sem role system
# ---------------------------------------------------------------
def test_folds_system_into_first_user_message() -> None:
    result = normalize_for(_exchange(), caps(supports_system_role=False))

    assert all(m.role != "system" for m in result)
    first_user = next(m for m in result if m.role == "user")
    assert "Você é o Age." in (first_user.content or "")
    assert "o que tenho amanhã?" in (first_user.content or "")


def test_injects_user_message_when_only_system_exists() -> None:
    messages = [Message(role="system", content="instruções")]
    result = normalize_for(messages, caps(supports_system_role=False))
    assert result[0].role == "user"
    assert "instruções" in (result[0].content or "")


# ---------------------------------------------------------------
# 5. Truncamento
# ---------------------------------------------------------------
def test_truncates_to_context_window_keeping_system() -> None:
    long_text = "palavra " * 2000  # ~4000 tokens estimados
    messages = [
        Message(role="system", content="sou o Age"),
        *[Message(role="user", content=long_text) for _ in range(10)],
    ]
    result = normalize_for(messages, caps(context_window=4_000))

    assert result[0].role == "system"
    assert len(result) < len(messages)


def test_truncation_inserts_summary_when_available() -> None:
    long_text = "x" * 20_000
    messages = [
        Message(role="system", content="sou o Age"),
        *[Message(role="user", content=long_text) for _ in range(5)],
    ]
    result = normalize_for(
        messages, caps(context_window=3_000), summary="Falamos de cálculo."
    )
    joined = " ".join(m.content or "" for m in result)
    assert "Falamos de cálculo." in joined


def test_truncation_never_starts_with_orphan_tool_result() -> None:
    """Um resultado de ferramenta abrindo a janela ficaria órfão."""
    long_text = "y" * 8_000
    messages = [
        Message(role="system", content="s"),
        Message(role="user", content=long_text),
        Message(
            role="assistant",
            tool_calls=(ToolCall(id="c1", name="t", arguments={}),),
        ),
        Message(role="tool", content=long_text, tool_call_id="c1"),
        Message(role="user", content="e agora?"),
    ]
    result = normalize_for(messages, caps(context_window=2_500))
    non_system = [m for m in result if m.role != "system"]
    if non_system:
        assert non_system[0].role != "tool"


# ---------------------------------------------------------------
# Matriz de destinos — o teste que cobre o requisito de §3.1.3
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    "destination",
    [
        caps(),  # tudo suportado
        caps(supports_tools=False),  # Ollama pequeno
        caps(supports_system_role=False),  # estilo Gemini
        caps(supports_parallel_tool_calls=False),
        caps(context_window=2_000),  # janela apertada
        caps(
            supports_tools=False,
            supports_system_role=False,
            context_window=2_000,
        ),  # pior caso combinado
    ],
    ids=["full", "no-tools", "no-system", "no-parallel", "tiny", "worst"],
)
def test_history_survives_any_destination(destination: ModelCapabilities) -> None:
    """Um histórico com tool calls normaliza sem erro para qualquer destino.

    É o que garante que "começo no DeepSeek, depois vou para o Gemini"
    funcione de fato, e não quebre com erro de API.
    """
    result = normalize_for(_exchange(), destination)

    assert result, "normalização não pode devolver histórico vazio"

    # Invariante central: nenhum par órfão sobra, em nenhum destino.
    requested = {tc.id for m in result for tc in m.tool_calls}
    answered = {m.tool_call_id for m in result if m.role == "tool"}
    assert answered <= requested

    if not destination.supports_tools:
        assert all(not m.tool_calls for m in result)
    if not destination.supports_system_role:
        assert all(m.role != "system" for m in result)


def test_normalize_is_idempotent() -> None:
    """Normalizar duas vezes dá o mesmo resultado.

    Importa porque o orquestrador normaliza a cada iteração do laço.
    """
    destination = caps(supports_tools=False)
    once = normalize_for(_exchange(), destination)
    twice = normalize_for(once, destination)
    assert [(m.role, m.content) for m in once] == [
        (m.role, m.content) for m in twice
    ]


def test_estimate_tokens_is_monotonic() -> None:
    assert estimate_tokens(None) == 0
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)
