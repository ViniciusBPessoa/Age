"""Validação de imagem recebida: pelos BYTES, nunca pelo que o cliente diz.

Isto duplica `domain/imagem.py` do ai-worker, e a duplicação é
deliberada. §2.4 proíbe os dois serviços de importarem código um do
outro, e promover uma função de 20 linhas a `packages/age-contracts`
acoplaria o contrato de API à detecção de formato — que é detalhe de
implementação, não contrato.

As duas cópias concordarem é verificado: `test_anexo_upload.py` checa
os quatro formatos contra os mesmos bytes que o teste do worker usa.

## Por que validar aqui, se o worker valida também

Porque o erro precisa chegar ao usuário ANTES de a mensagem ser enviada.
Se só o worker checasse, a pessoa anexaria o arquivo, escreveria a
pergunta, mandaria, esperaria — e receberia "não entendi o anexo" de um
turno já cobrado. A checagem no upload transforma isso numa recusa
imediata, com o arquivo ainda na tela.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

MediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]

# 5 MB. Igual ao CHECK da migration 0013: a rota recusa com uma frase
# útil, a constraint garante que nenhum outro caminho escape.
TAMANHO_MAXIMO = 5 * 1024 * 1024
# Quantas imagens cabem numa mensagem. A API do provedor aceita 600;
# o limite aqui é de interface — 4 miniaturas cabem no composer sem
# virar lista com rolagem, e 600 anexos num turno é outro produto.
MAXIMO_POR_MENSAGEM = 4

_ASSINATURAS: tuple[tuple[bytes, MediaType], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\x0a", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


@dataclass(frozen=True)
class ImagemValida:
    dados: bytes
    media_type: MediaType
    sha256: str
    tamanho: int


class ImagemRecusada(Exception):
    """Recusa com motivo legível, para a rota devolver ao usuário.

    A mensagem é escrita para quem anexou o arquivo, não para log: quem
    vê isto está olhando a tela com a imagem do lado.
    """

    def __init__(self, motivo: str) -> None:
        self.motivo = motivo
        super().__init__(motivo)


def detectar_media_type(dados: bytes) -> MediaType | None:
    """O tipo real dos bytes, ou `None` se não for imagem reconhecida."""
    if len(dados) < 4:
        return None
    for assinatura, tipo in _ASSINATURAS:
        if dados.startswith(assinatura):
            return tipo
    # WebP precisa de "RIFF" E dos bytes 8-12: WAV e AVI também são
    # contêineres RIFF, e aceitar o prefixo sozinho mandaria áudio para
    # um endpoint de imagem.
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "image/webp"
    return None


def validar(dados: bytes) -> ImagemValida:
    """Valida e descreve, ou levanta `ImagemRecusada`.

    A ordem importa: tamanho antes de formato. Um arquivo de 40 MB que
    não é imagem deve ouvir sobre o tamanho, porque é o que a pessoa
    pode resolver — e porque ler a assinatura de um arquivo enorme para
    então reclamar do tamanho é trabalho jogado fora.
    """
    if not dados:
        raise ImagemRecusada("O arquivo está vazio.")
    if len(dados) > TAMANHO_MAXIMO:
        mb = len(dados) / 1024 / 1024
        raise ImagemRecusada(
            f"A imagem tem {mb:.1f} MB e o limite é 5 MB. "
            "Tente reduzir a resolução antes de enviar."
        )
    tipo = detectar_media_type(dados)
    if tipo is None:
        raise ImagemRecusada(
            "Não reconheci este arquivo como imagem. Aceito JPEG, PNG, GIF e WebP."
        )
    return ImagemValida(
        dados=dados,
        media_type=tipo,
        sha256=hashlib.sha256(dados).hexdigest(),
        tamanho=len(dados),
    )
