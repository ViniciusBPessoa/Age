"""Publicação de eventos de turno no Redis Stream.

ARQUITETURA.md §2.3.1. O worker não tem conexão com o navegador: ele
publica aqui, e o core-api assina e repassa como SSE. Essa indireção é o
que permite reiniciar ou escalar o worker sem derrubar conexão de usuário.
"""

from __future__ import annotations

import logging

from age_contracts import StreamEvent
from redis.asyncio import Redis

from app.config import get_settings

log = logging.getLogger("ai-worker.publisher")


def turn_stream_key(turn_id: str) -> str:
    """Mesma convenção do core-api — se divergir, o relay não acha nada."""
    return f"turn:{turn_id}"


def turn_cancel_key(turn_id: str) -> str:
    return f"cancel:{turn_id}"


class TurnPublisher:
    """Escreve os eventos de um turno e expira o stream depois."""

    def __init__(self, redis: Redis, turn_id: str) -> None:
        self._redis = redis
        self._turn_id = turn_id
        self._key = turn_stream_key(turn_id)

    async def publish(self, event: StreamEvent) -> str:
        """Acrescenta um evento. Devolve o id atribuído pelo Redis.

        O id é sequencial e é o que o cliente usa como `last_event_id`
        para retomar depois de uma queda (§7.1).
        """
        msg_id = await self._redis.xadd(
            self._key,
            {"data": event.model_dump_json()},
        )
        return msg_id

    async def finish(self) -> None:
        """Marca o fim da vida útil do stream.

        TTL em vez de delete: o cliente pode reconectar depois do `done`
        e precisa reler o que perdeu. Uma hora é folga suficiente.
        """
        await self._redis.expire(self._key, get_settings().turn_stream_ttl_seconds)

    async def is_cancelled(self) -> bool:
        """Checado entre iterações do laço (§7.1).

        Cancelar é ação explícita do usuário; fechar a aba não cancela,
        porque os tokens já foram pagos quando a chamada saiu.
        """
        return bool(await self._redis.exists(turn_cancel_key(self._turn_id)))
