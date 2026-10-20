"""Reconhecimento de imagem por CONTEÚDO, não por extensão.

Isto é domínio puro: bytes entram, um veredito sai. Nada de rede, de
banco, nem de `PIL` — §10.2 proíbe infraestrutura aqui, e a verificação
que importa é de assinatura binária, que não precisa de biblioteca.

## Por que não confiar na extensão nem no `Content-Type`

Os dois vêm do cliente. Um `.png` com bytes de JPEG faria o provedor
devolver 400 depois de o usuário esperar o upload inteiro — erro que
custa tempo dele e nada diz. E um arquivo que não é imagem nenhuma,
renomeado para `.png`, não deve chegar perto do provedor.

A doc do DeepSeek é explícita: o formato é detectado pelo conteúdo. Este
módulo faz a mesma coisa, antes, para poder recusar com uma frase que a
pessoa entenda.

## O que NÃO é checado aqui, de propósito

Que a imagem seja *renderizável*. Um PNG com cabeçalho válido e dados
corrompidos passa por aqui e o provedor recusa. Validar de verdade
exigiria decodificar, ou seja, uma dependência e uma superfície de
ataque (decodificadores de imagem são alvo clássico). O erro do provedor
é a resposta aceitável para esse caso, porque é raro e não é perigoso.
"""

from __future__ import annotations

from typing import Literal

MediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]

# Os quatro formatos que o DeepSeek aceita (doc oficial, out/2026).
# A ordem não importa: as assinaturas são mutuamente exclusivas.
_ASSINATURAS: tuple[tuple[bytes, MediaType], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# O WebP é o único que precisa de dois pedaços: "RIFF", 4 bytes de
# tamanho que variam, e então "WEBP". Checar só "RIFF" aceitaria WAV e
# AVI, que também são contêineres RIFF.
_RIFF = b"RIFF"
_WEBP = b"WEBP"

# Bytes suficientes para decidir: a assinatura mais longa tem 8, e o
# WebP precisa de 12. Lê-se um pedaço pequeno, nunca o arquivo todo.
PREFIXO_NECESSARIO = 12


def detectar_media_type(dados: bytes) -> MediaType | None:
    """O tipo real dos bytes, ou `None` se não for imagem reconhecida.

    `None` significa "não sei o que é isto", e quem chama deve recusar.
    Nunca devolve um palpite: adivinhar aqui é mandar para o provedor um
    arquivo que ele vai rejeitar, com a conta já gasta no upload.
    """
    if len(dados) < 4:
        return None
    for assinatura, tipo in _ASSINATURAS:
        if dados.startswith(assinatura):
            return tipo
    if dados[:4] == _RIFF and dados[8:12] == _WEBP:
        return "image/webp"
    return None
