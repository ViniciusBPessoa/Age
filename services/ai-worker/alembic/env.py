"""Ambiente Alembic do ai-worker.

Duas coisas específicas desta arquitetura:

1. **Roda como `age_owner`, não como `ai_worker`.** As migrations criam e
   alteram estrutura; o runtime só lê e escreve dados. Separar as duas
   coisas é o que mantém o RLS válido (ARQUITETURA.md §4.2.1) — se o
   runtime fosse dono das tabelas, ignoraria as políticas.

2. **Só vê o schema `ai`.** `include_object` filtra o resto, para que um
   autogenerate aqui nunca proponha mexer em `core`.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

OWN_SCHEMA = os.getenv("DB_SCHEMA_OWN", "ai")


def _database_url() -> str:
    """URL de migration: usa MIGRATION_DATABASE_URL (age_owner) se houver."""
    url = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "MIGRATION_DATABASE_URL ou DATABASE_URL é obrigatório para migrar"
        )
    return url


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Mantém o autogenerate dentro do schema deste serviço."""
    if type_ == "table":
        return obj.schema == OWN_SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=OWN_SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        # Tabela de versão dentro do próprio schema: cada serviço tem a
        # sua, e uma migration nunca enxerga o histórico da outra.
        version_table_schema=OWN_SCHEMA,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    config.set_main_option("sqlalchemy.url", _database_url())
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
