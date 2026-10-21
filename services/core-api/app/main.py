"""Ponto de entrada do core-api.

Superfície pública do sistema: tudo em /api/v1/* (ARQUITETURA.md §7.3),
mais as rotas de webhook em /hooks/* que ficam FORA do grupo autenticado
(§7.2) porque chegam sem cookie e sem JWT.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

# Importar registra os adaptadores de calendario no registry (secao 3.2).
from app.adapters.calendar import in_memory, local  # noqa: F401
from app.api import (
    attachments,
    auth,
    calendar,
    conversations,
    echo,
    health,
    llm,
    plans,
    preferences,
    privacy,
    routines,
    study,
    tasks,
    usage,
)
from app.core import tracing
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.redis import close_redis

# Formato e filtro numa chamada só: era `basicConfig` com `%(trace_id)s`
# mais um `install()` separado, e a separação permitiu o formato entrar
# sem o filtro chegar aos handlers — toda linha de log de biblioteca
# morreu com `ValueError` até isso ser corrigido. Ver `core.tracing`.
tracing.configure_logging(logging.INFO)
log = logging.getLogger("core-api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # De novo aqui, e não só no topo do módulo: o uvicorn aplica o
    # LOGGING_CONFIG dele DEPOIS de importar a app, e aquilo
    # desconfigura `uvicorn.access`. O `lifespan` roda depois disso,
    # então é o último a falar — e é o que faz a linha de acesso sair
    # em JSON com trace, em vez do texto puro do uvicorn.
    tracing.configure_logging(logging.INFO)

    settings = get_settings()
    log.info("core-api subindo (env=%s)", settings.environment)
    yield
    await close_redis()
    await dispose_engine()
    log.info("core-api encerrado")


app = FastAPI(
    title="Age — core-api",
    version="0.1.0",
    lifespan=lifespan,
    # Docs ficam abertas em dev; em produção o serviço não é público.
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Primeiro da pilha, portanto o mais externo: o trace precisa existir
# antes da autenticação, porque um 401 é exatamente o tipo de resposta
# que se vai querer rastrear.
app.add_middleware(tracing.TraceMiddleware)

# /healthz e /readyz na raiz: o Compose e o orquestrador esperam ali,
# não sob /api/v1.
app.include_router(health.router)

# Superfície versionada desde o início: adicionar versão depois, com
# clientes no ar, custa caro (§7.0).
v1 = APIRouter(prefix="/api/v1")
v1.include_router(attachments.router)
v1.include_router(auth.router)
v1.include_router(llm.router)
v1.include_router(preferences.router)
v1.include_router(calendar.router)
v1.include_router(plans.router)
v1.include_router(study.router)
v1.include_router(tasks.router)
v1.include_router(routines.router)
v1.include_router(routines.reports)
v1.include_router(usage.router)
v1.include_router(routines.changes)
v1.include_router(privacy.router)
v1.include_router(conversations.router)
v1.include_router(conversations.turns)
# Mantido do M0 para o smoke test continuar valendo como diagnóstico de
# plumbing, independente de auth.
v1.include_router(echo.router)
app.include_router(v1)

# Rotas internas: fora de /api/v1 e fora do ingress (secao 7.3).
# O ai-worker le a agenda por aqui, com JWT de servico.
app.include_router(calendar.internal)
app.include_router(study.internal)
app.include_router(tasks.internal)
app.include_router(routines.internal)


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "core-api",
            "docs": "/docs",
            "health": "/healthz",
        }
    )
