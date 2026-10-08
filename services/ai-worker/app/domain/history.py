"""Normalização de histórico ao trocar de provedor.

ARQUITETURA.md §3.1.3. Esta é a função que faz o caso de uso central
funcionar: começar no DeepSeek e depois testar no ChatGPT ou Gemini.

Sem ela, a troca **falha com erro de API**, não com resposta pior:
- o histórico tem `tool_call_id` em formato do provedor antigo;
- um modelo sem tools recebe `role: "tool"` e rejeita;
- um modelo sem `system` recebe uma mensagem que não entende;
- um provedor sem parallel tool calls recebe dois calls num turno.

Função pura: nada de I/O, nada de SDK. É o que permite testar as N×N
combinações de troca sem rede (§10.1).
"""

from __future__ import annotations

from dataclasses import replace

from app.ports.llm import Message, ModelCapabilities, ToolCall


def normalize_for(
    messages: list[Message],
    caps: ModelCapabilities,
    *,
    summary: str | None = None,
    reserve_output_tokens: int = 1024,
) -> list[Message]:
    """Adapta um histórico canônico às limitações do provedor de destino.

    A ordem dos passos importa: narrar tool calls (passo 2) antes de
    sequenciar (passo 3) evitaria trabalho, mas sequenciar primeiro
    mantém o código de cada passo independente do outro.
    """
    result = _drop_orphan_tool_pairs(messages)

    if not caps.supports_tools:
        result = _narrate_tool_exchanges(result)
    elif not caps.supports_parallel_tool_calls:
        result = _split_parallel_tool_calls(result)

    if not caps.supports_system_role:
        result = _fold_system_into_first_user(result)

    result = _truncate_to_window(
        result,
        caps.context_window - reserve_output_tokens,
        summary=summary,
    )
    return result


# ---------------------------------------------------------------
# 1. Pares órfãos
# ---------------------------------------------------------------
def _drop_orphan_tool_pairs(messages: list[Message]) -> list[Message]:
    """Remove tool call sem resultado, e resultado sem call.

    Todo provedor rejeita histórico com chamada pendente. Isso acontece
    de verdade quando um turno é cancelado no meio (§7.1).
    """
    answered: set[str] = {
        m.tool_call_id
        for m in messages
        if m.role == "tool" and m.tool_call_id
    }
    requested: set[str] = {
        tc.id for m in messages for tc in m.tool_calls
    }

    out: list[Message] = []
    for msg in messages:
        if msg.role == "tool":
            if msg.tool_call_id in requested:
                out.append(msg)
            continue

        if msg.tool_calls:
            kept = tuple(tc for tc in msg.tool_calls if tc.id in answered)
            if not kept:
                # Nenhuma chamada foi respondida. Se havia texto junto,
                # preserva o texto; senão descarta a mensagem.
                if msg.content:
                    out.append(replace(msg, tool_calls=()))
                continue
            out.append(replace(msg, tool_calls=kept))
            continue

        out.append(msg)
    return out


# ---------------------------------------------------------------
# 2. Destino sem tools: narrar
# ---------------------------------------------------------------
def _narrate_tool_exchanges(messages: list[Message]) -> list[Message]:
    """Converte troca de ferramenta em texto.

    Preserva o raciocínio anterior em vez de apagá-lo: o modelo novo não
    sabe chamar ferramenta, mas ainda se beneficia de saber o que foi
    consultado e o que voltou.
    """
    results: dict[str, str] = {
        m.tool_call_id: (m.content or "")
        for m in messages
        if m.role == "tool" and m.tool_call_id
    }

    out: list[Message] = []
    for msg in messages:
        if msg.role == "tool":
            continue

        if msg.tool_calls:
            lines = [
                f"- {tc.name}({_short_args(tc)}) → "
                f"{_short(results.get(tc.id, 'sem resultado'))}"
                for tc in msg.tool_calls
            ]
            narration = "[Consultas feitas anteriormente:\n" + "\n".join(lines) + "\n]"
            content = f"{msg.content}\n\n{narration}" if msg.content else narration
            out.append(Message(role="assistant", content=content))
            continue

        out.append(msg)
    return out


def _short_args(tc: ToolCall, limit: int = 80) -> str:
    if not tc.arguments:
        return ""
    text = ", ".join(f"{k}={v}" for k, v in tc.arguments.items())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------
# 3. Destino sem parallel tool calls: sequenciar
# ---------------------------------------------------------------
def _split_parallel_tool_calls(messages: list[Message]) -> list[Message]:
    """Reescreve um turno com N chamadas como N turnos de 1 chamada."""
    results: dict[str, Message] = {
        m.tool_call_id: m
        for m in messages
        if m.role == "tool" and m.tool_call_id
    }
    consumed: set[str] = set()

    out: list[Message] = []
    for msg in messages:
        if msg.role == "tool":
            if msg.tool_call_id in consumed:
                continue
            out.append(msg)
            continue

        if len(msg.tool_calls) > 1:
            for index, tc in enumerate(msg.tool_calls):
                # O texto acompanha só o primeiro, para não duplicar.
                out.append(
                    Message(
                        role="assistant",
                        content=msg.content if index == 0 else None,
                        tool_calls=(tc,),
                    )
                )
                answer = results.get(tc.id)
                if answer is not None:
                    out.append(answer)
                    consumed.add(tc.id)
            continue

        out.append(msg)
    return out


# ---------------------------------------------------------------
# 4. Destino sem role `system`
# ---------------------------------------------------------------
def _fold_system_into_first_user(messages: list[Message]) -> list[Message]:
    """Vira prefixo da primeira mensagem de usuário."""
    system_parts = [m.content for m in messages if m.role == "system" and m.content]
    if not system_parts:
        return messages

    prefix = "\n\n".join(system_parts)
    rest = [m for m in messages if m.role != "system"]

    for index, msg in enumerate(rest):
        if msg.role == "user":
            merged = replace(
                msg, content=f"{prefix}\n\n---\n\n{msg.content or ''}".strip()
            )
            return [*rest[:index], merged, *rest[index + 1 :]]

    # Sem nenhuma mensagem de usuário: injeta uma.
    return [Message(role="user", content=prefix), *rest]


# ---------------------------------------------------------------
# 5. Truncamento por janela de contexto
# ---------------------------------------------------------------
def estimate_tokens(text: str | None) -> int:
    """Estimativa grosseira: ~4 caracteres por token.

    Suficiente para decidir truncamento. Contar exato exigiria o
    tokenizador de cada provedor — dependência que a porta existe para
    evitar, e precisão que não muda a decisão.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _message_tokens(msg: Message) -> int:
    total = estimate_tokens(msg.content) + 4  # overhead de role/estrutura
    for tc in msg.tool_calls:
        total += estimate_tokens(tc.name) + estimate_tokens(str(tc.arguments)) + 8
    return total


def _truncate_to_window(
    messages: list[Message],
    budget: int,
    *,
    summary: str | None,
) -> list[Message]:
    """Preserva o system prompt e as trocas mais recentes.

    O miolo descartado é substituído pelo resumo, quando houver
    (§6.1 `conversation_summaries`).
    """
    if budget <= 0:
        return messages

    system = [m for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]

    used = sum(_message_tokens(m) for m in system)
    if summary:
        used += estimate_tokens(summary) + 8

    kept: list[Message] = []
    for msg in reversed(rest):
        cost = _message_tokens(msg)
        if used + cost > budget and kept:
            break
        used += cost
        kept.append(msg)
    kept.reverse()

    dropped = len(rest) - len(kept)
    if dropped <= 0:
        return [*system, *rest]

    bridge: list[Message] = []
    if summary:
        bridge.append(
            Message(
                role="system",
                content=f"[Resumo do início da conversa]\n{summary}",
            )
        )
    else:
        bridge.append(
            Message(
                role="system",
                content=(
                    f"[{dropped} mensagens anteriores foram omitidas por "
                    "limite de contexto.]"
                ),
            )
        )

    # Um resultado de ferramenta não pode abrir a janela: ficaria órfão.
    while kept and kept[0].role == "tool":
        kept.pop(0)

    return [*system, *bridge, *kept]
