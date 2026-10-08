"""Testes do cofre de segredos.

ARQUITETURA.md §4.3. O teste que mais importa é o de isolamento por
contexto: ele verifica que um blob cifrado para um usuário não
descriptografa no contexto de outro, mesmo que alguém consiga mover a
linha entre contas no banco.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.crypto import (
    DecryptionError,
    SecretBox,
    llm_context,
    oauth_context,
)

_KEY = "a" * 48
_OTHER_KEY = "b" * 48


def test_roundtrip() -> None:
    box = SecretBox(_KEY)
    secret = "sk-deepseek-abc123xyz"
    blob = box.encrypt(secret, "llm:user-1")
    assert box.decrypt(blob, "llm:user-1") == secret


def test_ciphertext_is_not_plaintext() -> None:
    box = SecretBox(_KEY)
    secret = "sk-muito-secreto"
    blob = box.encrypt(secret, "llm:user-1")
    assert secret.encode() not in blob


def test_same_plaintext_gives_different_ciphertext() -> None:
    """Salt e nonce aleatórios por registro.

    Sem isso, duas pessoas com a mesma chave teriam o mesmo blob — e
    quem olhasse o banco saberia disso.
    """
    box = SecretBox(_KEY)
    a = box.encrypt("mesmo-segredo", "llm:user-1")
    b = box.encrypt("mesmo-segredo", "llm:user-1")
    assert a != b
    assert box.decrypt(a, "llm:user-1") == box.decrypt(b, "llm:user-1")


def test_wrong_context_fails() -> None:
    """O teste central: AAD amarra o ciphertext ao contexto.

    Mover a linha de um usuário para outro no banco não dá acesso ao
    segredo.
    """
    box = SecretBox(_KEY)
    user_a, user_b = uuid4(), uuid4()
    blob = box.encrypt("sk-do-usuario-a", llm_context(user_a))

    with pytest.raises(DecryptionError):
        box.decrypt(blob, llm_context(user_b))


def test_wrong_master_key_fails() -> None:
    blob = SecretBox(_KEY).encrypt("segredo", "ctx")
    with pytest.raises(DecryptionError):
        SecretBox(_OTHER_KEY).decrypt(blob, "ctx")


def test_tampered_ciphertext_fails() -> None:
    """GCM detecta alteração: é cifra autenticada."""
    box = SecretBox(_KEY)
    blob = bytearray(box.encrypt("segredo", "ctx"))
    blob[-1] ^= 0x01  # vira um bit do tag
    with pytest.raises(DecryptionError):
        box.decrypt(bytes(blob), "ctx")


def test_truncated_blob_fails_cleanly() -> None:
    """Dado corrompido no banco dá erro tratado, não IndexError."""
    box = SecretBox(_KEY)
    with pytest.raises(DecryptionError):
        box.decrypt(b"\x01\x02", "ctx")


def test_unknown_version_fails() -> None:
    """Permite rotacionar o esquema de cifra sem ambiguidade."""
    box = SecretBox(_KEY)
    blob = bytearray(box.encrypt("segredo", "ctx"))
    blob[0] = 0xFF
    with pytest.raises(DecryptionError):
        box.decrypt(bytes(blob), "ctx")


def test_contexts_are_distinct() -> None:
    """Contexto de LLM e de OAuth não se confundem."""
    user = uuid4()
    assert llm_context(user) != oauth_context(user, "google")
    assert oauth_context(user, "google") != oauth_context(user, "microsoft")


def test_unicode_secret_roundtrip() -> None:
    box = SecretBox(_KEY)
    secret = "chave-com-acentuação-e-emoji-🔑"
    blob = box.encrypt(secret, "ctx")
    assert box.decrypt(blob, "ctx") == secret
