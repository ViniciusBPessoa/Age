"""Cofre de segredos: cifra credenciais de API em repouso.

ARQUITETURA.md §4.3. Duas decisões que importam:

1. **AAD por contexto.** O GCM aceita dados autenticados adicionais, e
   usamos `f"llm:{user_id}"` como contexto. Isso significa que um blob
   cifrado para um usuário NÃO descriptografa no contexto de outro —
   mesmo que alguém consiga mover a linha entre contas no banco.

2. **Salt por registro.** A chave de cifra é derivada da `MASTER_KEY` via
   HKDF com salt aleatório por segredo, guardado junto do ciphertext.
   Sem isso, todos os segredos compartilhariam a mesma chave derivada.

Perder a `MASTER_KEY` significa perder todos os segredos. Não há
recuperação, e isso é dito ao usuário no onboarding.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32  # AES-256
_VERSION = b"\x01"  # permite rotacionar o esquema sem ambiguidade


class DecryptionError(Exception):
    """Falha ao decifrar: chave errada, contexto errado ou dado corrompido."""


@dataclass(frozen=True)
class SecretBox:
    """Cifra e decifra segredos com uma chave mestra."""

    master_key: str

    def _derive(self, salt: bytes, context: str) -> bytes:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=_KEY_LEN,
            salt=salt,
            info=context.encode("utf-8"),
        )
        return hkdf.derive(self.master_key.encode("utf-8"))

    def encrypt(self, plaintext: str, context: str) -> bytes:
        """Formato: versão(1) ‖ salt(16) ‖ nonce(12) ‖ ciphertext+tag."""
        salt = os.urandom(_SALT_LEN)
        nonce = os.urandom(_NONCE_LEN)
        key = self._derive(salt, context)
        blob = AESGCM(key).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            context.encode("utf-8"),  # AAD: amarra o ciphertext ao contexto
        )
        return _VERSION + salt + nonce + blob

    def decrypt(self, data: bytes, context: str) -> str:
        if len(data) < 1 + _SALT_LEN + _NONCE_LEN:
            raise DecryptionError("blob truncado")
        if data[:1] != _VERSION:
            raise DecryptionError(f"versão de cifra não suportada: {data[:1]!r}")

        offset = 1
        salt = data[offset : offset + _SALT_LEN]
        offset += _SALT_LEN
        nonce = data[offset : offset + _NONCE_LEN]
        offset += _NONCE_LEN
        blob = data[offset:]

        key = self._derive(salt, context)
        try:
            plain = AESGCM(key).decrypt(nonce, blob, context.encode("utf-8"))
        except Exception as exc:
            # Mensagem deliberadamente vaga: distinguir "chave errada" de
            # "contexto errado" daria informação a quem estiver sondando.
            raise DecryptionError("não foi possível decifrar o segredo") from exc
        return plain.decode("utf-8")


def llm_context(user_id: object) -> str:
    """Contexto de cifra para credencial de LLM."""
    return f"llm:{user_id}"


def oauth_context(user_id: object, provider: str) -> str:
    """Contexto de cifra para token OAuth de calendário ou tarefas."""
    return f"oauth:{provider}:{user_id}"
