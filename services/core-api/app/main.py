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
    auth,
    calendar,
    conversations,
    echo,
    health,
    llm,
    plans,
    preferences,
    study,
)
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.redis import close_redis

logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("core-api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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

# /healthz e /readyz na raiz: o Compose e o orquestrador esperam ali,
# não sob /api/v1.
app.include_router(health.router)

# Superfície versionada desde o início: adicionar versão depois, com
# clientes no ar, custa caro (§7.0).
v1 = APIRouter(prefix="/api/v1")
v1.include_router(auth.router)
v1.include_router(llm.router)
v1.include_router(preferences.router)
v1.include_router(calendar.router)
v1.include_router(plans.router)
v1.include_router(study.router)
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


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "core-api",
            "docs": "/docs",
            "health": "/healthz",
        }
    )
