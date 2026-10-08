"""O laço de conversa.

ARQUITETURA.md §5.1. Recebe a mensagem, monta o contexto, chama o
modelo e executa ferramentas até ele parar de pedir.

Duas regras estruturais, válidas desde já mesmo sem ferramentas de
agenda (que chegam no M2):

1. **Nenhuma tool de escrita toca nada.** Escrita só produz item de
   plano, que o usuário aprova depois (§5.2).
2. **Tool call é acumulado inteiro antes de executar.** Streaming de
   texto vai ao usuário na hora; fragmento de tool call não serve de
   nada pela metade.

O laço vive aqui, no núcleo, sem conhecer HTTP nem Redis: quem publica é
o chamador (`tasks/run_turn.py`). É o que permite testá-lo sem rede.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.domain.history import normalize_for
from app.ports.llm import (
    LLMProvider,
    MalformedToolCall,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)

log = logging.getLogger("ai-worker.orchestrator")

# Callbacks que o chamador fornece para publicar no stream.
EmitText = Callable[[str], Awaitable[None]]
EmitToolStart = Callable[[str], Awaitable[None]]
EmitToolEnd = Callable[[str, bool, str | None], Awaitable[None]]
IsCancelled = Callable[[], Awaitable[bool]]

# Executor de ferramenta: recebe nome e argumentos, devolve o resultado
# serializável. No M2 isto passa a ser o registry de tools reais.
ToolExecutor = Callable[[str, dict], Awaitable[str]]


@dataclass
class TurnResult:
    text: str
    usage: Usage
    finish_reason: str
    iterations: int
    tool_calls_made: int
    cancelled: bool = False
    messages_appended: list[Message] = field(default_factory=list)


class TurnCancelled(Exception):
    """O usuário cancelou explicitamente (§7.1)."""


async def run_conversation_turn(
    *,
    provider: LLMProvider,
    messages: list[Message],
    tools: list[ToolSpec] | None,
    execute_tool: ToolExecutor,
    emit_text: EmitText,
    emit_tool_start: EmitToolStart,
    emit_tool_end: EmitToolEnd,
    is_cancelled: IsCancelled,
    max_iterations: int = 8,
    summary: str | None = None,
    temperature: float = 0.3,
) -> TurnResult:
    """Roda um turno completo até o modelo parar de pedir ferramenta."""
    caps = provider.capabilities

    # Histórico canônico -> formato que ESTE provedor aceita (§3.1.3).
    working = normalize_for(
        messages, caps, summary=summary, reserve_output_tokens=caps.max_output_tokens
    )

    usable_tools = tools if (tools and caps.supports_tools) else None

    accumulated_text: list[str] = []
    appended: list[Message] = []
    total_usage = Usage(prompt_tokens=0, completion_tokens=0)
    tool_calls_made = 0
    malformed_retries = 0
    iteration = 0
    finish_reason = "stop"

    while iteration < max_iterations:
        iteration += 1

        if await is_cancelled():
            raise TurnCancelled()

        text_parts: list[str] = []
        pending: dict[int, dict] = {}
        chunk_finish: str | None = None

        try:
            async for chunk in provider.stream(
                working,
                usable_tools,
                temperature=temperature,
                max_tokens=caps.max_output_tokens,
            ):
                if chunk.text_delta:
                    text_parts.append(chunk.text_delta)
                    await emit_text(chunk.text_delta)

                if chunk.tool_call_delta:
                    _accumulate_tool_delta(pending, chunk.tool_call_delta)

                if chunk.usage:
                    total_usage = Usage(
                        prompt_tokens=total_usage.prompt_tokens
                        + chunk.usage.prompt_tokens,
                        completion_tokens=total_usage.completion_tokens
                        + chunk.usage.completion_tokens,
                    )

                if chunk.finish_reason:
                    chunk_finish = chunk.finish_reason

        except MalformedToolCall:
            # Acontece de verdade com `finish_reason: "length"`. Uma
            # tentativa de correção, e só (§3.1.1).
            if malformed_retries >= 1:
                raise
            malformed_retries += 1
            log.info("tool call malformado, pedindo correção ao modelo")
            working = [
                *working,
                Message(
                    role="user",
                    content=(
                        "Sua última chamada de ferramenta tinha JSON inválido. "
                        "Repita usando JSON válido e completo."
                    ),
                ),
            ]
            continue

        finish_reason = chunk_finish or "stop"
        turn_text = "".join(text_parts)
        if turn_text:
            accumulated_text.append(turn_text)

        calls = _finalize_tool_calls(pending)

        # Sem ferramenta pedida: o turno acabou.
        if not calls:
            final = Message(role="assistant", content=turn_text or None)
            working.append(final)
            appended.append(final)
            break

        # Com ferramenta: registra a intenção e executa as de leitura.
        assistant_msg = Message(
            role="assistant",
            content=turn_text or None,
            tool_calls=tuple(calls),
        )
        working.append(assistant_msg)
        appended.append(assistant_msg)

        for call in calls:
            if await is_cancelled():
                raise TurnCancelled()

            tool_calls_made += 1
            await emit_tool_start(call.name)
            try:
                output = await execute_tool(call.name, call.arguments)
                ok, summary_text = True, None
            except Exception as exc:  # noqa: BLE001
                # Falha de ferramenta volta ao modelo como dado, não
                # derruba o turno: ele pode tentar outro caminho.
                log.warning("ferramenta %s falhou: %s", call.name, exc)
                output = json.dumps({"error": str(exc)}, ensure_ascii=False)
                ok, summary_text = False, str(exc)[:120]

            await emit_tool_end(call.name, ok, summary_text)
            tool_msg = Message(
                role="tool", content=output, tool_call_id=call.id
            )
            working.append(tool_msg)
            appended.append(tool_msg)

    else:
        # Estourou o limite de iterações (§5.1).
        finish_reason = "length"
        note = (
            "Cheguei ao limite de consultas neste turno. "
            "Posso continuar se você me der mais contexto."
        )
        await emit_text("\n\n" + note)
        accumulated_text.append("\n\n" + note)
        final = Message(role="assistant", content=note)
        appended.append(final)

    return TurnResult(
        text="".join(accumulated_text),
        usage=total_usage,
        finish_reason=finish_reason,
        iterations=iteration,
        tool_calls_made=tool_calls_made,
        messages_appended=appended,
    )


# ---------------------------------------------------------------
# Acumulação de deltas de tool call
# ---------------------------------------------------------------
def _accumulate_tool_delta(pending: dict[int, dict], delta: dict) -> None:
    """Concatena fragmentos por índice.

    Provedores compatíveis com OpenAI mandam o nome num chunk e os
    argumentos picados em vários. Parsear antes do fim daria JSON
    truncado — a causa mais comum de `MalformedToolCall` evitável.
    """
    index = delta.get("index", 0)
    slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})

    if delta.get("id"):
        slot["id"] = delta["id"]

    fn = delta.get("function") or {}
    if fn.get("name"):
        slot["name"] = fn["name"]
    if fn.get("arguments"):
        slot["arguments"] += fn["arguments"]


def _finalize_tool_calls(pending: dict[int, dict]) -> list[ToolCall]:
    """Parseia os argumentos acumulados, agora que estão completos."""
    calls: list[ToolCall] = []
    for index in sorted(pending):
        slot = pending[index]
        if not slot["name"]:
            continue

        raw = slot["arguments"].strip()
        if not raw:
            arguments: dict = {}
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise MalformedToolCall(
                    f"argumentos de '{slot['name']}' não formam JSON válido"
                ) from exc
            if not isinstance(parsed, dict):
                raise MalformedToolCall(
                    f"argumentos de '{slot['name']}' não são um objeto"
                )
            arguments = parsed

        calls.append(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=arguments,
            )
        )
    return calls
