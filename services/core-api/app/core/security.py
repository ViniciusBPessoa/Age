"""Senhas e tokens.

ARQUITETURA.md §4.1.1 e §4.1.2.

O desenho de token tem três peças que não devem ser confundidas:

- **access** (15 min): vai no header `Authorization` do BFF para o
  core-api. Curto porque não é revogável — expira e pronto.
- **refresh** (30 dias): vive só no cookie httpOnly, rotaciona a cada uso.
  É revogável, porque existe linha em `core.sessions`.
- **family_id**: agrupa a cadeia de refreshes de uma sessão. Se um
  refresh já usado reaparecer, a família inteira é invalidada — é a
  detecção de roubo de §4.1.2.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

# Parâmetros acima do mínimo recomendado pela OWASP, confortáveis para a
# escala do projeto (login não é caminho quente).
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

_ALGORITHM = "HS256"


# ---------------------------------------------------------------
# Senhas
# ---------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True quando os parâmetros mudaram e vale reescrever o hash no login."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return False


# ---------------------------------------------------------------
# Tokens opacos (refresh, verificação de e-mail, reset de senha)
# ---------------------------------------------------------------
def new_opaque_token() -> str:
    """Token aleatório de 32 bytes, em base64url."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Guarda no banco só o hash.

    SHA-256 puro, sem Argon2: o token já tem 256 bits de entropia, então
    não há ataque de dicionário a mitigar — e um hash rápido permite
    buscar por índice.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), stored_hash)


# ---------------------------------------------------------------
# JWT de acesso
# ---------------------------------------------------------------
class TokenError(Exception):
    """Token inválido, expirado ou com claims inesperados."""


def create_access_token(
    user_id: UUID,
    session_id: UUID,
    *,
    role: str = "user",
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int(
            (
                now + timedelta(seconds=settings.access_token_ttl_seconds)
            ).timestamp()
        ),
    }
    return jwt.encode(payload, settings.jwt_signing_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_key,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub", "sid"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token inválido") from exc

    if payload.get("typ") != "access":
        raise TokenError("tipo de token inesperado")
    return payload


# ---------------------------------------------------------------
# JWT de serviço (core-api -> ai-worker)
# ---------------------------------------------------------------
def create_service_token(audience: Literal["ai-worker"]) -> str:
    """Token de vida curta para chamada entre serviços (§2.3.1)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "iss": "core-api",
        "aud": audience,
        "typ": "service",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
    }
    return jwt.encode(payload, settings.service_jwt_secret, algorithm=_ALGORITHM)
