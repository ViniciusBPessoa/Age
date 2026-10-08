"""Consumidor da fila — o processo principal do ai-worker.

Executado como `arq app.worker.WorkerSettings`.

ARQUITETURA.md §2.3: este serviço existe separado porque o perfil de
execução é outro. Uma chamada a provedor de LLM pode levar 40 s ou
pendurar; um GET /api/plans leva 15 ms. Misturados num processo, um turno
travado consome worker de request HTTP e degrada a UI inteira.
"""

from __future__ import annotations

import logging

from age_contracts import JobName
from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

# Importar os adaptadores registra cada um no registry (§3.1). Sem estes
# imports o registry fica vazio e `build_llm` não acha nada.
from app.adapters.llm import deepseek, mock, openai  # noqa: F401
from app.config import get_settings
from app.db import dispose_engine
from app.tasks.echo import echo
from app.tasks.maintenance import purge_conversations, title_conversation
from app.tasks.run_turn import run_turn

logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("ai-worker")


async def startup(ctx: dict) -> None:
    settings = get_settings()
    # Cliente próprio para o stream de eventos: o pool do ARQ é da fila.
    ctx["redis"] = Redis.from_url(str(settings.redis_url), decode_responses=True)
    log.info("ai-worker pronto (concorrência=%s)", settings.worker_concurrency)


async def shutdown(ctx: dict) -> None:
    redis: Redis | None = ctx.get("redis")
    if redis is not None:
        await redis.aclose()
    await dispose_engine()
    log.info("ai-worker encerrado")


# O ARQ identifica a função pelo nome; renomear quebra jobs já
# enfileirados. Por isso o nome vem do contrato, não do nome da função.
run_turn.__name__ = JobName.RUN_TURN.value
title_conversation.__name__ = JobName.TITLE_CONVERSATION.value
purge_conversations.__name__ = JobName.PURGE_CONVERSATIONS.value
echo.__name__ = JobName.ECHO.value


class WorkerSettings:
    functions = [run_turn, title_conversation, purge_conversations, echo]

    # Expurgo diário às 4h: fora do horário de uso, e a janela de 30 dias
    # torna o atraso de algumas horas irrelevante (§6.2.4).
    cron_jobs = [
        cron(purge_conversations, hour=4, minute=0, run_at_startup=False),
    ]

    on_startup = startup
    on_shutdown = shutdown

    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    max_jobs = get_settings().worker_concurrency

    # Timeout duro por job: guarda contra turno pendurado, já que o
    # worker não detecta desconexão do navegador (§7.1).
    job_timeout = get_settings().turn_timeout_seconds

    # Mantém resultado por 1 h, para o core-api poder consultar estado.
    keep_result = 3600

    # Sem retry automático em job de LLM: reprocessar cobra tokens de
    # novo. Falha é reportada ao usuário, que decide se tenta outra vez.
    max_tries = 1
