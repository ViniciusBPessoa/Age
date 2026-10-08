"""Regras de autenticação: registro, login e rotação de refresh.

ARQUITETURA.md §4.1.1 e §4.1.2.

A parte delicada é `rotate_refresh_token`. Dois problemas precisam ser
resolvidos ao mesmo tempo:

1. **Detecção de roubo.** Se um refresh já usado reaparece, alguém tem
   uma cópia. Invalidamos a família inteira.
2. **Corrida de renovação.** Várias chamadas paralelas do BFF podem
   receber 401 juntas e tentar renovar ao mesmo tempo. Sem cuidado, a
   primeira rotaciona, as outras apresentam o token velho — e a detecção
   de roubo derruba a sessão legítima.

O (2) é resolvido com lock no Redis mais uma janela de graça: um token
recém-rotacionado continua aceito por alguns segundos, devolvendo o par
que já foi emitido.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_token,
    new_opaque_token,
    verify_password,
)
from app.models import Session as SessionModel
from app.models import User

log = logging.getLogger("core-api.auth")

# Janela em que um token recém-rotacionado ainda é aceito, para absorver
# requisições paralelas que saíram antes da rotação.
_GRACE_SECONDS = 20


class AuthError(Exception):
    """Falha de autenticação, com código legível por máquina."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    session_id: UUID
    expires_at: datetime


async def authenticate(
    db: AsyncSession, email: str, password: str
) -> User:
    """Valida credenciais.

    A mensagem de erro é a mesma para e-mail inexistente e senha errada:
    distinguir permitiria enumerar quem tem conta.
    """
    result = await db.execute(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if user is None or user.password_hash is None:
        raise AuthError("invalid_credentials", "E-mail ou senha incorretos.")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid_credentials", "E-mail ou senha incorretos.")

    return user


async def issue_session(
    db: AsyncSession,
    user: User,
    *,
    device_label: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    family_id: UUID | None = None,
) -> TokenPair:
    """Cria uma sessão nova e o primeiro par de tokens."""
    settings = get_settings()
    now = datetime.now(UTC)
    refresh = new_opaque_token()
    session_id = uuid4()
    expires_at = now + timedelta(seconds=settings.refresh_token_ttl_seconds)

    db.add(
        SessionModel(
            id=session_id,
            user_id=user.id,
            refresh_token_hash=hash_token(refresh),
            token_family_id=family_id or uuid4(),
            device_label=device_label,
            ip=ip,
            user_agent=user_agent,
            expires_at=expires_at,
            last_used_at=now,
        )
    )
    await db.flush()

    return TokenPair(
        access_token=create_access_token(user.id, session_id, role=user.role),
        refresh_token=refresh,
        session_id=session_id,
        expires_at=expires_at,
    )


async def rotate_refresh_token(
    db: AsyncSession,
    redis: Redis,
    refresh_token: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    """Troca um refresh válido por um par novo, rotacionando.

    Esta função é chamada sem usuário autenticado — o próprio token é a
    credencial. Por isso usa sessão de sistema e filtra por hash.
    """
    token_hash = hash_token(refresh_token)
    now = datetime.now(UTC)

    result = await db.execute(
        select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
    )
    session = result.scalar_one_or_none()

    if session is None:
        # Token desconhecido: ou é forjado, ou é de uma família já
        # invalidada e apagada. Não dá informação sobre qual.
        raise AuthError("invalid_refresh", "Sessão inválida. Entre novamente.")

    # --- Reuso: a sessão já foi rotacionada ---
    if session.revoked_at is not None:
        reused_at = session.revoked_at
        within_grace = (now - reused_at).total_seconds() <= _GRACE_SECONDS

        if within_grace and session.revoked_reason == "rotated":
            # Corrida benigna (§4.1.2): outra requisição paralela já
            # rotacionou. Devolve o par vigente da família em vez de
            # tratar como roubo.
            successor = await _latest_active_in_family(db, session.token_family_id)
            if successor is not None:
                user = await _load_user(db, successor.user_id)
                return TokenPair(
                    access_token=create_access_token(
                        user.id, successor.id, role=user.role
                    ),
                    # O refresh não é reemitido: o cookie vigente já é o
                    # do sucessor, posto pela requisição que ganhou.
                    refresh_token="",
                    session_id=successor.id,
                    expires_at=successor.expires_at,
                )

        # Reuso fora da janela: alguém tem uma cópia do token.
        log.warning(
            "reuso de refresh token detectado family=%s user=%s",
            session.token_family_id,
            session.user_id,
        )
        await _revoke_family(db, session.token_family_id, reason="reuse_detected")
        raise AuthError(
            "refresh_reused",
            "Detectamos uso suspeito desta sessão. Entre novamente.",
        )

    if session.expires_at <= now:
        raise AuthError("refresh_expired", "Sua sessão expirou. Entre novamente.")

    # --- Rotação normal ---
    await db.execute(
        update(SessionModel)
        .where(SessionModel.id == session.id)
        .values(revoked_at=now, revoked_reason="rotated", last_used_at=now)
    )

    user = await _load_user(db, session.user_id)
    return await issue_session(
        db,
        user,
        device_label=session.device_label,
        ip=ip or session.ip,
        user_agent=user_agent or session.user_agent,
        family_id=session.token_family_id,  # mantém a cadeia
    )


async def revoke_session(
    db: AsyncSession, session_id: UUID, *, reason: str = "logout"
) -> None:
    await db.execute(
        update(SessionModel)
        .where(SessionModel.id == session_id, SessionModel.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )


def refresh_lock_key(token_hash: str) -> str:
    """Lock por token, não por usuário.

    Por usuário seria mais largo que o necessário: duas abas com sessões
    diferentes não competem entre si.
    """
    return f"refresh_lock:{token_hash[:32]}"


# ---------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------
async def _load_user(db: AsyncSession, user_id: UUID) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise AuthError("user_not_found", "Conta não encontrada.")
    return user


async def _latest_active_in_family(
    db: AsyncSession, family_id: UUID
) -> SessionModel | None:
    result = await db.execute(
        select(SessionModel)
        .where(
            SessionModel.token_family_id == family_id,
            SessionModel.revoked_at.is_(None),
        )
        .order_by(SessionModel.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _revoke_family(
    db: AsyncSession, family_id: UUID, *, reason: str
) -> None:
    await db.execute(
        update(SessionModel)
        .where(
            SessionModel.token_family_id == family_id,
            SessionModel.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
