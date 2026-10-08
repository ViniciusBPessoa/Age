"""Sessões de banco com Row-Level Security imposta.

ARQUITETURA.md §4.2.2. Este módulo é a ÚNICA forma de obter uma sessão.
Nenhum repositório instancia `_session_factory` diretamente — a regra é
verificada em CI (§10.2).

Por que isso importa: a política de RLS depende de `app.user_id` estar
setado na transação. Se alguém abrir uma sessão crua e esquecer de setar,
`current_setting('app.user_id')` LANÇA ERRO (note: sem o segundo
argumento, de propósito). Falhar alto é melhor que falhar aberto — uma
rota quebrada é incidente; uma política que devolve NULL silenciosamente
é vazamento entre contas.
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

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base declarativa. Tabelas vivem no schema `core`."""

    __table_args__ = {"schema": "core"}


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
    """Sessão restrita a um usuário. É o caminho normal.

    A transação é aberta EXPLICITAMENTE com `session.begin()` antes do
    `set_config`. Isso não é detalhe de estilo: `set_config(..., true)`
    tem escopo de transação (equivale a `SET LOCAL`), então se o
    SQLAlchemy encerrar a transação implícita entre o set e as queries,
    o valor desaparece e a política de RLS passa a não ver linha
    nenhuma. O sintoma é confuso — `new row violates row-level security
    policy` num INSERT perfeitamente legítimo.

    Escopo de transação também é o que impede o valor de vazar para a
    próxima requisição que pegar a mesma conexão do pool.
    """
    async with _factory()() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"),
                {"uid": str(user_id)},
            )
            yield session
            # O commit é do `session.begin()`; rollback automático em
            # exceção.


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    """Sessão sem usuário autenticado.

    Dois usos legítimos:
      - **bootstrap:** registro, login e rotação de refresh token
        acontecem antes de haver sessão (§4.1).
      - **varredura global:** expirar planos, expurgar conversas.

    NÃO usa BYPASSRLS. As políticas de §4.2 tratam a ausência de
    `app.user_id` como "nenhuma linha visível" — falha fechada —, e as
    operações de bootstrap têm política explícita e estreita (busca por
    e-mail exato ou por hash de token, nunca listagem).

    O `set_config` para string vazia é defensivo: a conexão vem do pool
    e pode ter valor residual de uma transação anterior. Sem isso, um
    `system_session` herdaria o usuário da requisição que usou a
    conexão antes.
    """
    async with _factory()() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.user_id', '', true)")
            )
            yield session


async def dispose_engine() -> None:
    """Fecha o pool. Chamado no shutdown da aplicação."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
