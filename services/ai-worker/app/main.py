"""Superfície HTTP mínima do ai-worker.

ARQUITETURA.md §7.3: este serviço NÃO é exposto no ingress. Só o mínimo,
na rede interna, protegido por JWT de serviço. Todo o resto do trabalho
chega por fila.

Roda em processo separado do consumidor (`app.worker`): o Compose sobe os
dois com comandos diferentes a partir da mesma imagem.
"""

from __future__ import annotations

import logging

import jwt
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text

# Importar registra os adaptadores no registry (§3.1).
from app.adapters.llm import deepseek, mock, openai  # noqa: F401
from app.adapters.llm.registry import available_providers, build_llm
from app.config import get_settings
from app.db import get_engine
from app.ports.llm import AuthenticationError, LLMError
from app.publishers import turn_cancel_key

logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("ai-worker.api")

app = FastAPI(title="Age — ai-worker (interno)", version="0.1.0")


async def require_service_token(
    authorization: str = Header(default=""),
) -> None:
    """Autenticação entre serviços (§2.3.1).

    JWT assinado com `SERVICE_JWT_SECRET`, vida de 60 s, emitido pelo
    core-api. Não é o secret em claro: um JWT expira, o secret não — se
    vazasse de um log, valeria para sempre.

    `aud` é verificado para que um token destinado a outro serviço não
    sirva aqui.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Token de serviço ausente.",
                }
            },
        )

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token,
            get_settings().service_jwt_secret,
            algorithms=["HS256"],
            audience="ai-worker",
            options={"require": ["exp", "aud"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Token de serviço inválido.",
                }
            },
        ) from exc

    if payload.get("typ") != "service":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Tipo de token inesperado.",
                }
            },
        )


internal = APIRouter(
    prefix="/internal", dependencies=[Depends(require_service_token)]
)


@internal.post("/turns/{turn_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_turn(turn_id: str) -> dict[str, str]:
    """Marca o turno para abortar na próxima iteração do laço.

    Não mata o job: deixa o laço sair limpo, publicando o evento de
    cancelamento para o usuário ver.
    """
    settings = get_settings()
    redis = Redis.from_url(str(settings.redis_url), decode_responses=True)
    try:
        await redis.setex(turn_cancel_key(turn_id), settings.turn_timeout_seconds, "1")
    finally:
        await redis.aclose()
    return {"turn_id": turn_id, "status": "cancel_requested"}


class VerifyRequest(BaseModel):
    """Verificação de credencial, chamada pelo core-api.

    A chave em claro trafega só na rede interna do Compose, numa
    requisição com JWT de serviço. O core-api precisa disto porque os
    adaptadores vivem no worker (§3.4) — ele não sabe falar com
    provedor nenhum.
    """

    provider_key: str = Field(min_length=1, max_length=50)
    api_key: str = Field(min_length=1, max_length=500)
    model: str | None = None
    base_url: str | None = None


class VerifyResponse(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str | None = None
    capabilities: dict | None = None


@internal.post("/llm/verify")
async def verify_credential(body: VerifyRequest) -> VerifyResponse:
    """Valida a credencial gastando o mínimo de tokens possível."""
    try:
        provider = build_llm(
            body.provider_key,
            api_key=body.api_key,
            model=body.model,
            base_url=body.base_url,
        )
    except LLMError as exc:
        return VerifyResponse(ok=False, error_code=exc.code, message=str(exc))

    try:
        ok = await provider.health_check()
        caps = provider.capabilities
        return VerifyResponse(
            ok=ok,
            capabilities={
                "supports_tools": caps.supports_tools,
                "context_window": caps.context_window,
                "cost_per_1m_input": caps.cost_per_1m_input,
                "cost_per_1m_output": caps.cost_per_1m_output,
            },
        )
    except AuthenticationError as exc:
        return VerifyResponse(
            ok=False,
            error_code=exc.code,
            message="A chave foi rejeitada pelo provedor.",
        )
    except LLMError as exc:
        return VerifyResponse(ok=False, error_code=exc.code, message=str(exc))
    finally:
        await provider.aclose()


@internal.get("/llm/providers")
async def list_providers() -> list[dict]:
    """Catálogo do registry, para o onboarding e o seletor (§8.1)."""
    return available_providers()


@internal.get("/llm/instance-key")
async def instance_key_status() -> dict:
    """Se o operador configurou uma chave de instância (§3451).

    Perguntado pelo core-api, e não lido de uma variável de ambiente
    dele: a chave vive só aqui, porque só o worker fala com provedor de
    LLM (§3.4). Duplicar a variável nos dois serviços criaria dois
    lugares para desalinhar — o core-api diria "tem chave" e o turno
    falharia com `no_credential`.

    **Nunca devolve a chave**, nem os últimos dígitos: o front não tem o
    que fazer com eles, e credencial do operador não é informação de
    usuário. Só se existe, de qual provedor e qual modelo — que é o que
    a tela precisa para dizer "usando o modelo X, por conta da casa".
    """
    settings = get_settings()
    if not settings.has_instance_key:
        return {"available": False}
    return {
        "available": True,
        "provider_key": settings.instance_llm_provider,
        "model": settings.instance_llm_model.strip() or None,
    }


app.include_router(internal)


@app.get("/internal/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "ai-worker"}


@app.get("/internal/readyz")
async def readyz() -> dict[str, object]:
    """Checa o que o worker precisa para trabalhar.

    No M1 isto passa a checar também os provedores de LLM configurados.
    """
    checks: dict[str, str] = {}

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {type(exc).__name__}"

    redis = Redis.from_url(str(get_settings().redis_url), decode_responses=True)
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    finally:
        await redis.aclose()

    return {"ready": all(v == "ok" for v in checks.values()), "checks": checks}
