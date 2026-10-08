"""Conexões Redis: fila de jobs e stream de eventos de turno.

ARQUITETURA.md §2.3.1. Duas vias distintas:
  - fila (ARQ)           core-api -> ai-worker
  - Redis Stream         ai-worker -> core-api -> browser
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from redis.asyncio import Redis

from app.core.config import get_settings

_redis: Redis | None = None
_arq: ArqRedis | None = None


def get_redis() -> Redis:
    """Cliente para leitura de stream e chaves auxiliares."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            str(get_settings().redis_url), decode_responses=True
        )
    return _redis


async def get_arq() -> ArqRedis:
    """Pool de enfileiramento. O core-api só produz; nunca consome."""
    global _arq
    if _arq is None:
        _arq = await create_pool(
            RedisSettings.from_dsn(str(get_settings().redis_url))
        )
    return _arq


def turn_stream_key(turn_id: str) -> str:
    """Canal de eventos de um turno.

    Um Redis Stream, não pub/sub: o stream guarda histórico com ids
    sequenciais, o que torna a retomada por `last_event_id` trivial
    (§7.1). Com pub/sub, perder o Wi-Fi perderia o turno.
    """
    return f"turn:{turn_id}"


def turn_cancel_key(turn_id: str) -> str:
    """Flag de cancelamento, checada pelo worker entre iterações (§7.1)."""
    return f"cancel:{turn_id}"


async def close_redis() -> None:
    global _redis, _arq
    if _redis is not None:
        await _redis.aclose()
        _redis = None
    if _arq is not None:
        await _arq.aclose()
        _arq = None
