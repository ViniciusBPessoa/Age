"""Testes de senha e token.

ARQUITETURA.md §4.1.1.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

# As settings exigem segredos; definimos antes de importar o módulo.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("JWT_SIGNING_KEY", "j" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.core.security import (  # noqa: E402
    TokenError,
    create_access_token,
    create_service_token,
    decode_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    tokens_match,
    verify_password,
)


# ---------------------------------------------------------------
# Senhas
# ---------------------------------------------------------------
def test_password_roundtrip() -> None:
    hashed = hash_password("senha-bem-comprida-123")
    assert verify_password("senha-bem-comprida-123", hashed)
    assert not verify_password("senha-errada", hashed)


def test_hash_is_salted() -> None:
    """Mesma senha, hashes diferentes."""
    a = hash_password("mesma-senha-123")
    b = hash_password("mesma-senha-123")
    assert a != b
    assert verify_password("mesma-senha-123", a)
    assert verify_password("mesma-senha-123", b)


def test_verify_handles_garbage_hash() -> None:
    """Hash corrompido no banco devolve False, não explode."""
    assert verify_password("qualquer", "não-é-um-hash") is False
    assert verify_password("qualquer", "") is False


def test_hash_does_not_contain_password() -> None:
    hashed = hash_password("segredo-literal-aqui")
    assert "segredo-literal-aqui" not in hashed


# ---------------------------------------------------------------
# Tokens opacos
# ---------------------------------------------------------------
def test_opaque_tokens_are_unique_and_long() -> None:
    tokens = {new_opaque_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 40 for t in tokens)


def test_token_hash_comparison() -> None:
    token = new_opaque_token()
    stored = hash_token(token)
    assert tokens_match(token, stored)
    assert not tokens_match(new_opaque_token(), stored)


def test_token_hash_is_deterministic() -> None:
    """Determinístico porque precisa ser buscável por índice."""
    token = new_opaque_token()
    assert hash_token(token) == hash_token(token)


# ---------------------------------------------------------------
# JWT
# ---------------------------------------------------------------
def test_access_token_roundtrip() -> None:
    user_id, session_id = uuid4(), uuid4()
    token = create_access_token(user_id, session_id, role="user")
    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["sid"] == str(session_id)
    assert payload["role"] == "user"
    assert payload["typ"] == "access"


def test_tampered_token_rejected() -> None:
    token = create_access_token(uuid4(), uuid4())
    head, body, sig = token.split(".")
    with pytest.raises(TokenError):
        decode_access_token(f"{head}.{body}.{sig[:-4]}xxxx")


def test_garbage_token_rejected() -> None:
    with pytest.raises(TokenError):
        decode_access_token("isto-não-é-um-jwt")


def test_service_token_is_not_accepted_as_access() -> None:
    """Separar os tipos evita confusão de privilégio.

    Um token de serviço vale para a rota interna do worker; aceitá-lo
    como token de usuário seria escalada.
    """
    service = create_service_token("ai-worker")
    with pytest.raises(TokenError):
        decode_access_token(service)


def test_expired_token_message_is_distinguishable() -> None:
    """O BFF reage diferente a expirado: dispara renovação (§4.1.2)."""
    import time

    import jwt

    from app.core.config import get_settings

    settings = get_settings()
    payload = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "typ": "access",
        "iat": int(time.time()) - 100,
        "exp": int(time.time()) - 10,
    }
    expired = jwt.encode(payload, settings.jwt_signing_key, algorithm="HS256")

    with pytest.raises(TokenError, match="expirado"):
        decode_access_token(expired)
