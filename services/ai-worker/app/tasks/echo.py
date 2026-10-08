"""Job de eco — prova a plumbing do M0.

Não é produto. Será substituído por `run_turn` (o orquestrador real) no
M1. Existe para exercitar exatamente o caminho que o produto vai usar:
consumir da fila, publicar eventos no stream, respeitar cancelamento.

O formato dos eventos é o mesmo de um turno real (ARQUITETURA.md §7.1),
então o front construído contra isto continua valendo no M1.
"""

from __future__ import annotations

import asyncio
import logging

from age_contracts import (
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
    TurnStatus,
    UsageEvent,
)

from app.publishers import TurnPublisher

log = logging.getLogger("ai-worker.echo")


async def echo(ctx: dict, payload: dict) -> dict:
    """Simula um turno: duas 'ferramentas' e depois texto, token a token.

    A sequência imita o caso real: as primeiras iterações são silenciosas
    (só tool_start/tool_end) e só depois vem texto. É por isso que esses
    eventos existem — sem eles o usuário encara segundos de nada (§5.1).
    """
    turn_id = payload["turn_id"]
    message = payload["message"]
    pub = TurnPublisher(ctx["redis"], turn_id)

    log.info("echo iniciado turn_id=%s", turn_id)

    try:
        # Fase silenciosa: imita leitura de agenda e busca de slots.
        for tool in ("get_events", "find_free_slots"):
            if await pub.is_cancelled():
                await _cancel(pub)
                return {"status": "cancelled"}

            await pub.publish(ToolStartEvent(name=tool))
            await asyncio.sleep(0.4)
            await pub.publish(
                ToolEndEvent(name=tool, ok=True, summary="ok (simulado)")
            )

        # Fase de texto: streaming token a token.
        resposta = f"Recebi sua mensagem: {message}"
        for palavra in resposta.split():
            if await pub.is_cancelled():
                await _cancel(pub)
                return {"status": "cancelled"}

            await pub.publish(TokenEvent(delta=palavra + " "))
            await asyncio.sleep(0.08)

        await pub.publish(
            UsageEvent(
                prompt_tokens=0,
                completion_tokens=0,
                model="echo",
                provider_key="mock",
            )
        )
        await pub.publish(
            DoneEvent(message_id=turn_id, status=TurnStatus.DONE)
        )
        return {"status": "done"}

    except Exception:
        log.exception("echo falhou turn_id=%s", turn_id)
        await pub.publish(
            ErrorEvent(
                code="internal_error",
                message="Algo falhou ao processar. Tente novamente.",
                retryable=True,
            )
        )
        raise
    finally:
        # Sempre aplica o TTL, inclusive em falha: stream órfão sem
        # expiração vaza memória no Redis.
        await pub.finish()


async def _cancel(pub: TurnPublisher) -> None:
    from age_contracts import DoneEvent as _Done

    await pub.publish(
        _Done(message_id="", status=TurnStatus.CANCELLED)
    )
