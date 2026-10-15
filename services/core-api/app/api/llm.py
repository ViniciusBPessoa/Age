"""Rotas de credenciais de LLM.

ARQUITETURA.md §4.3. A regra central: depois de salva, a chave **nunca**
volta pela API. O usuário vê só os 4 últimos caracteres, para reconhecer
qual é.

Verificar a credencial exige falar com o provedor, e os adaptadores vivem
no ai-worker (§3.4). Então esta rota chama o endpoint interno do worker
com JWT de serviço — a chave em claro trafega só na rede do Compose.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text, update

from app.core.config import get_settings
from app.core.crypto import SecretBox, llm_context
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import enforce
from app.core.security import create_service_token
from app.models import LLMCredential

log = logging.getLogger("core-api.llm")
router = APIRouter(prefix="/llm", tags=["llm"])


class ProviderInfo(BaseModel):
    key: str
    name: str
    models: list[str]
    default_model: str


class KeySourceOut(BaseModel):
    """De onde vem a chave usada nas mensagens deste usuário (§3451).

    O que a tela de Configurações precisa para contar a verdade: se o
    uso é por conta da casa ou da chave do próprio usuário, e quanto da
    cota sobrou. Sem isto a pessoa bateria num limite invisível.
    """

    # "user" | "instance" | "none"
    source: str
    # Preenchidos só quando `source == "instance"`.
    provider_key: str | None = None
    model: str | None = None
    # Cota em USD. `limit_usd = None` significa ilimitado — o que vale
    # para quem usa a própria chave E para contas liberadas pelo
    # operador. `used_usd` é o gasto do mês corrente pago pela casa.
    used_usd: float | None = None
    limit_usd: float | None = None


class CredentialIn(BaseModel):
    provider_key: str = Field(min_length=1, max_length=50)
    api_key: str = Field(min_length=8, max_length=500)
    default_model: str | None = Field(default=None, max_length=100)
    base_url: str | None = Field(default=None, max_length=300)
    label: str | None = Field(default=None, max_length=80)


class CredentialOut(BaseModel):
    """Note a ausência de `api_key`. É deliberada (§4.3)."""

    id: UUID
    provider_key: str
    key_last4: str
    default_model: str | None
    base_url: str | None
    label: str | None
    is_active: bool
    last_verified_at: datetime | None
    last_error: str | None
    created_at: datetime


class VerifyResult(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str | None = None
    capabilities: dict | None = None


async def _worker_call(path: str, payload: dict | None = None) -> dict:
    """Chamada ao endpoint interno do worker, com JWT de serviço (§2.3.1)."""
    settings = get_settings()
    url = f"{settings.ai_worker_internal_url}{path}"
    headers = {"Authorization": f"Bearer {create_service_token('ai-worker')}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = (
                await client.post(url, json=payload, headers=headers)
                if payload is not None
                else await client.get(url, headers=headers)
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": "worker_unavailable",
                        "message": "O serviço de IA está indisponível. "
                        "Tente em alguns segundos.",
                    }
                },
            ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": {
                    "code": "worker_error",
                    "message": "O serviço de IA recusou a requisição.",
                }
            },
        )
    return response.json()


@router.get("/providers")
async def list_providers(user: CurrentUser) -> list[ProviderInfo]:
    """Catálogo vindo do registry do worker (§3.1)."""
    data = await _worker_call("/internal/llm/providers")
    return [ProviderInfo(**item) for item in data]


@router.get("/key-source")
async def key_source(user: CurrentUser, db: DbSession) -> KeySourceOut:
    """De qual chave este usuário depende, e quanto da cota sobrou.

    A precedência replicada aqui é a MESMA de
    `ai-worker/credential_resolver.py`, e isso é uma duplicação que vale
    reconhecer: se as duas divergirem, a tela mente. Está coberta por
    teste que compara os dois caminhos — a alternativa seria o core-api
    perguntar ao worker a cada carregamento da tela, o que acrescenta um
    salto de rede a uma leitura puramente informativa.

    A cota é somada aqui, e não no worker, porque `core_api` já tem
    SELECT em `ai.turn_usage` (verificado em
    `information_schema.role_table_grants`) e porque esta é a única
    consulta que a tela precisa.
    """
    propria = (
        await db.execute(
            select(LLMCredential.id)
            .where(
                LLMCredential.user_id == user.id,
                LLMCredential.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if propria is not None:
        # Chave do usuário: sem cota, porque o custo é dele.
        return KeySourceOut(source="user")

    instancia = await _worker_call("/internal/llm/instance-key")
    if not instancia.get("available"):
        return KeySourceOut(source="none")

    limite_row = (
        await db.execute(
            text("SELECT monthly_quota_usd FROM core.users WHERE id = :uid"),
            {"uid": str(user.id)},
        )
    ).first()
    limite = limite_row[0] if limite_row and limite_row[0] is not None else None

    usado_row = (
        await db.execute(
            text(
                """
                SELECT COALESCE(SUM(estimated_cost_usd), 0)
                FROM ai.turn_usage
                WHERE user_id = :uid
                  AND used_instance_key = true
                  AND created_at >= date_trunc('month', now())
                """
            ),
            {"uid": str(user.id)},
        )
    ).first()

    return KeySourceOut(
        source="instance",
        provider_key=instancia.get("provider_key"),
        model=instancia.get("model"),
        used_usd=float(usado_row[0]) if usado_row else 0.0,
        # 0 no banco significa ilimitado, igual a NULL (migration 0010);
        # a API expõe os dois como `null`, porque "sem limite" é um
        # estado e não o número zero.
        limit_usd=float(limite) if limite else None,
    )


@router.get("/credentials")
async def list_credentials(user: CurrentUser, db: DbSession) -> list[CredentialOut]:
    rows = await db.execute(
        select(LLMCredential)
        .where(LLMCredential.user_id == user.id)
        .order_by(LLMCredential.created_at.asc())
    )
    return [
        CredentialOut.model_validate(c, from_attributes=True)
        for c in rows.scalars()
    ]


@router.post("/credentials", status_code=status.HTTP_201_CREATED)
async def upsert_credential(
    body: CredentialIn, user: CurrentUser, db: DbSession
) -> CredentialOut:
    """Salva (ou substitui) a chave de um provedor.

    Upsert em vez de erro no conflito: trocar uma chave que expirou é o
    caso comum, e obrigar a apagar antes seria atrito sem motivo.
    """
    settings = get_settings()
    blob = SecretBox(settings.master_key).encrypt(
        body.api_key, llm_context(user.id)
    )
    last4 = body.api_key[-4:]

    existing = (
        await db.execute(
            select(LLMCredential).where(
                LLMCredential.user_id == user.id,
                LLMCredential.provider_key == body.provider_key,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        await db.execute(
            update(LLMCredential)
            .where(LLMCredential.id == existing.id)
            .values(
                encrypted_api_key=blob,
                key_last4=last4,
                default_model=body.default_model or existing.default_model,
                base_url=body.base_url,
                label=body.label,
                is_active=True,
                last_verified_at=None,  # chave nova precisa reverificar
                last_error=None,
            )
        )
        credential_id = existing.id
    else:
        credential_id = uuid4()
        db.add(
            LLMCredential(
                id=credential_id,
                user_id=user.id,
                provider_key=body.provider_key,
                encrypted_api_key=blob,
                key_last4=last4,
                default_model=body.default_model,
                base_url=body.base_url,
                label=body.label,
            )
        )

    await db.flush()
    row = (
        await db.execute(select(LLMCredential).where(LLMCredential.id == credential_id))
    ).scalar_one()
    return CredentialOut.model_validate(row, from_attributes=True)


@router.post("/credentials/{credential_id}/verify")
async def verify_credential(
    credential_id: UUID, user: CurrentUser, db: DbSession
) -> VerifyResult:
    """Testa a chave contra o provedor, gastando o mínimo de tokens.

    Chamada no onboarding antes de deixar avançar: descobrir que a chave
    está errada na primeira mensagem é pior (§8.1).

    Limitado por usuário, e mais baixo que o resto (§2219): cada
    verificação é uma chamada real ao provedor com a chave do usuário.
    Sem limite, um laço nesta rota consome a cota dele — o custo cai em
    quem não fez nada. É o tipo de rota em que o limite protege o dono
    da chave, não o servidor.
    """
    settings = get_settings()

    await enforce(
        "llm:verify",
        str(user.id),
        settings.rate_limit_llm_verify_per_user,
        "Muitas verificações de chave. Aguarde um momento.",
    )
    cred = (
        await db.execute(
            select(LLMCredential).where(
                LLMCredential.id == credential_id,
                LLMCredential.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "credential_not_found",
                    "message": "Credencial não encontrada.",
                }
            },
        )

    api_key = SecretBox(settings.master_key).decrypt(
        bytes(cred.encrypted_api_key), llm_context(user.id)
    )
    result = await _worker_call(
        "/internal/llm/verify",
        {
            "provider_key": cred.provider_key,
            "api_key": api_key,
            "model": cred.default_model,
            "base_url": cred.base_url,
        },
    )

    await db.execute(
        update(LLMCredential)
        .where(LLMCredential.id == cred.id)
        .values(
            last_verified_at=datetime.now(UTC) if result["ok"] else None,
            last_error=None if result["ok"] else result.get("message"),
            is_active=result["ok"],
        )
    )
    return VerifyResult(**result)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: UUID, user: CurrentUser, db: DbSession
) -> None:
    await db.execute(
        delete(LLMCredential).where(
            LLMCredential.id == credential_id,
            LLMCredential.user_id == user.id,
        )
    )
