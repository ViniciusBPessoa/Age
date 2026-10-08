"""Sessões de banco do ai-worker.

Espelha o módulo do core-api, com uma diferença crítica: aqui a leitura
cruzada do schema `core` TAMBÉM passa por RLS (ARQUITETURA.md §4.2.3).

O motivo é concreto: se um bug trocasse o `user_id` do job, o worker
montaria o prompt de uma pessoa com a agenda de outra — e o vazamento
sairia pela resposta do LLM, não por uma query. Setar `app.user_id` no
início do job fecha isso.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Tabelas do worker vivem no schema `ai`."""

    __table_args__ = {"schema": "ai"}


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url,
            pool_size=s.db_pool_size,
            pool_pre_ping=True,
            echo=s.db_echo,
        )
    return _engine


def _factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, autoflush=False
        )
    return _session_factory


@asynccontextmanager
async def user_session(user_id: UUID) -> AsyncIterator[AsyncSession]:
    """Sessão restrita ao usuário dono do job.

    Todo job que lê ou escreve dado de usuário abre por aqui, com o
    `user_id` vindo do payload da fila.
    """
    async with _factory()() as session:
        # Transação EXPLÍCITA antes do set_config: o valor tem escopo de
        # transação, então encerrar a implícita no meio o apagaria — e a
        # política passaria a não ver linha nenhuma.
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"),
                {"uid": str(user_id)},
            )
            yield session


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    """Para jobs de varredura global: expirar planos, expurgar conversas."""
    async with _factory()() as session:
        async with session.begin():
            # Limpa valor residual da conexão do pool: sem isto, um job
            # de varredura herdaria o usuário do job anterior.
            await session.execute(
                text("SELECT set_config('app.user_id', '', true)")
            )
            yield session


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
