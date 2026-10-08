"""Rotas de autenticação.

ARQUITETURA.md §4.1. O cookie de refresh é posto e lido AQUI, não no
BFF — o Next só repassa `Set-Cookie` adiante. Isso mantém a decisão de
segurança num lugar só.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import system_session
from app.core.deps import CurrentUser, DbSession, client_ip
from app.core.redis import get_redis
from app.core.security import hash_password, needs_rehash
from app.models import Session as SessionModel
from app.models import User, UserPreferences
from app.services.auth import (
    AuthError,
    authenticate,
    issue_session,
    refresh_lock_key,
    revoke_session,
    rotate_refresh_token,
)

log = logging.getLogger("core-api.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "age_refresh"


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        _REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,  # o JS da página nunca lê
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(_REFRESH_COOKIE, path="/")


def _auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": exc.code, "message": exc.message}},
    )


# ---------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    # 10 caracteres em vez de 8: o ganho de segurança é real e o custo
    # para o usuário é desprezível.
    password: str = Field(min_length=10, max_length=200)
    name: str | None = Field(default=None, max_length=120)
    timezone: str = Field(default="America/Sao_Paulo", max_length=60)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int


class MeResponse(BaseModel):
    id: UUID
    email: str
    name: str | None
    timezone: str
    locale: str
    role: str
    email_verified: bool
    onboarding_step: int


class SessionInfo(BaseModel):
    id: UUID
    device_label: str | None
    ip: str | None
    created_at: datetime
    last_used_at: datetime | None
    is_current: bool


# ---------------------------------------------------------------
# Registro e login
# ---------------------------------------------------------------
@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, response: Response
) -> TokenResponse:
    """Cria a conta e já entra.

    Pedir login logo após registrar é atrito sem ganho: a pessoa acabou
    de provar que sabe a senha.
    """
    settings = get_settings()
    email = body.email.lower()

    # Sessão de sistema: ainda não existe usuário para o RLS.
    async with system_session() as db:
        existing = await db.execute(
            select(User.id).where(User.email == email, User.deleted_at.is_(None))
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "email_taken",
                        "message": "Já existe uma conta com este e-mail.",
                    }
                },
            )

        user_id = uuid4()
        db.add(
            User(
                id=user_id,
                email=email,
                password_hash=hash_password(body.password),
                name=body.name,
                timezone=body.timezone,
            )
        )
        # Preferências com os defaults do esquema, criadas junto: evita
        # checar "existe?" em toda leitura depois.
        db.add(UserPreferences(user_id=user_id))
        await db.flush()

        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        pair = await issue_session(
            db,
            user,
            device_label=_device_label(request),
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )

    _set_refresh_cookie(response, pair.refresh_token)
    log.info("conta criada user=%s", user_id)
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response
) -> TokenResponse:
    settings = get_settings()
    async with system_session() as db:
        try:
            user = await authenticate(db, body.email.lower(), body.password)
        except AuthError as exc:
            raise _auth_error(exc) from exc

        # Parâmetros de hash evoluem; o login é o momento natural de
        # reescrever com os atuais.
        if user.password_hash and needs_rehash(user.password_hash):
            user.password_hash = hash_password(body.password)

        user.last_login_at = datetime.now(UTC)
        pair = await issue_session(
            db,
            user,
            device_label=_device_label(request),
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )

    _set_refresh_cookie(response, pair.refresh_token)
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/refresh")
async def refresh(request: Request, response: Response) -> TokenResponse:
    """Renova o access token. Chamado pelo BFF, não pelo browser.

    O lock no Redis resolve a corrida de §4.1.2: várias chamadas
    paralelas recebendo 401 juntas tentariam rotacionar ao mesmo tempo
    e, com rotação estrita, invalidariam a sessão legítima.
    """
    settings = get_settings()
    token = request.cookies.get(_REFRESH_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "no_refresh_token",
                    "message": "Sessão não encontrada. Entre novamente.",
                }
            },
        )

    from app.core.security import hash_token

    redis = get_redis()
    lock_key = refresh_lock_key(hash_token(token))
    got_lock = await redis.set(lock_key, "1", nx=True, ex=5)

    if not got_lock:
        # Outra requisição está rotacionando. Esperar o resultado dela é
        # melhor que competir.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "refresh_in_progress",
                    "message": "Renovação em andamento. Tente novamente.",
                }
            },
            headers={"Retry-After": "1"},
        )

    try:
        async with system_session() as db:
            pair = await rotate_refresh_token(
                db,
                redis,
                token,
                ip=client_ip(request),
                user_agent=request.headers.get("User-Agent"),
            )
    except AuthError as exc:
        _clear_refresh_cookie(response)
        raise _auth_error(exc) from exc
    finally:
        await redis.delete(lock_key)

    # Vazio quando a corrida foi resolvida devolvendo o par vigente: o
    # cookie já está correto, não reescrever.
    if pair.refresh_token:
        _set_refresh_cookie(response, pair.refresh_token)

    return TokenResponse(
        access_token=pair.access_token,
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, db: DbSession, response: Response) -> None:
    await revoke_session(db, user.session_id, reason="logout")
    _clear_refresh_cookie(response)


# ---------------------------------------------------------------
# Perfil e sessões
# ---------------------------------------------------------------
@router.get("/me")
async def me(user: CurrentUser, db: DbSession) -> MeResponse:
    row = (
        await db.execute(select(User).where(User.id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "user_not_found",
                    "message": "Conta não encontrada.",
                }
            },
        )
    return MeResponse(
        id=row.id,
        email=row.email,
        name=row.name,
        timezone=row.timezone,
        locale=row.locale,
        role=row.role,
        email_verified=row.email_verified,
        onboarding_step=row.onboarding_step,
    )


class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=60)
    onboarding_step: int | None = Field(default=None, ge=0, le=3)


@router.patch("/me")
async def update_me(
    body: UpdateMeRequest, user: CurrentUser, db: DbSession
) -> MeResponse:
    values = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if values:
        await db.execute(update(User).where(User.id == user.id).values(**values))
    return await me(user, db)


@router.get("/sessions")
async def list_sessions(user: CurrentUser, db: DbSession) -> list[SessionInfo]:
    rows = await db.execute(
        select(SessionModel)
        .where(SessionModel.user_id == user.id, SessionModel.revoked_at.is_(None))
        .order_by(SessionModel.created_at.desc())
    )
    return [
        SessionInfo(
            id=s.id,
            device_label=s.device_label,
            ip=s.ip,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            is_current=(s.id == user.session_id),
        )
        for s in rows.scalars()
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_one(
    session_id: UUID, user: CurrentUser, db: DbSession, response: Response
) -> None:
    await revoke_session(db, session_id, reason="revoked_by_user")
    if session_id == user.session_id:
        _clear_refresh_cookie(response)


# ---------------------------------------------------------------
def _device_label(request: Request) -> str:
    """Rótulo legível para a lista de dispositivos.

    Heurística simples e suficiente: o objetivo é a pessoa reconhecer
    "foi este aparelho", não identificar o navegador com precisão.
    """
    ua = request.headers.get("User-Agent", "")
    if not ua:
        return "Dispositivo desconhecido"

    system = "Windows" if "Windows" in ua else (
        "macOS" if "Mac OS" in ua else (
            "Android" if "Android" in ua else (
                "iOS" if ("iPhone" in ua or "iPad" in ua) else (
                    "Linux" if "Linux" in ua else "Sistema desconhecido"
                )
            )
        )
    )
    browser = "Edge" if "Edg/" in ua else (
        "Chrome" if "Chrome" in ua else (
            "Firefox" if "Firefox" in ua else (
                "Safari" if "Safari" in ua else "Navegador"
            )
        )
    )
    return f"{browser} no {system}"
