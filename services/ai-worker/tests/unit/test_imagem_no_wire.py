"""A imagem atravessando o contrato: detecção, codificação e cache.

Cada teste traz a sabotagem que precisa derrubá-lo.
"""

from __future__ import annotations

import base64
import struct
import zlib

import pytest

from app.adapters.llm.openai_compatible import OpenAICompatibleProvider
from app.domain.imagem import detectar_media_type
from app.ports.llm import ImagePart, Message, TextPart


def png_bytes(lado: int = 4) -> bytes:
    """Um PNG real, montado à mão — sem depender de PIL no teste."""
    linhas = b"".join(b"\x00" + b"\xff\x00\x00" * lado for _ in range(lado))

    def chunk(tipo: bytes, dados: bytes) -> bytes:
        c = tipo + dados
        return struct.pack(">I", len(dados)) + c + struct.pack(">I", zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(linhas))
        + chunk(b"IEND", b"")
    )


class TestDeteccao:
    @pytest.mark.parametrize(
        ("dados", "esperado"),
        [
            (b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg"),
            (png_bytes(), "image/png"),
            (b"GIF89a" + b"\x00" * 20, "image/gif"),
            (b"GIF87a" + b"\x00" * 20, "image/gif"),
            (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
        ],
    )
    def test_reconhece_os_quatro_formatos(self, dados: bytes, esperado: str) -> None:
        """Sabotagem: remover qualquer entrada de `_ASSINATURAS`.

        O GIF tem DUAS assinaturas (87a e 89a) e esquecer uma recusaria
        metade dos GIFs do mundo com "não é imagem".
        """
        assert detectar_media_type(dados) == esperado

    def test_riff_que_nao_e_webp_e_recusado(self) -> None:
        """Sabotagem: checar só `RIFF` e não os bytes 8-12.

        WAV e AVI também são RIFF. Esta é a sabotagem que os testes de
        reconhecimento NÃO pegam: aceitar `RIFF` sozinho faz todos eles
        continuarem passando, e manda áudio para um endpoint de imagem.
        """
        wav = b"RIFF\x24\x08\x00\x00WAVEfmt "
        assert detectar_media_type(wav) is None

    @pytest.mark.parametrize(
        "dados",
        [
            b"",
            b"\x00",
            b"nao sou imagem nenhuma",
            # Um executável renomeado para .png continua sendo um
            # executável: a extensão não entra nesta conta.
            b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00",
            # PNG com UM byte errado na assinatura.
            b"\x89PNG\r\n\x1a\x00" + b"\x00" * 20,
        ],
    )
    def test_recusa_o_que_nao_e_imagem(self, dados: bytes) -> None:
        """Sabotagem: devolver um default (`"image/png"`) em vez de `None`.

        `None` é o que permite recusar com uma frase útil; um palpite
        manda o arquivo ao provedor e troca o erro por um 400 pago.
        """
        assert detectar_media_type(dados) is None


class TestCodificacao:
    def test_texto_puro_continua_string_no_wire(self) -> None:
        """Sabotagem: sempre devolver lista, mesmo para `str`.

        Passaria em qualquer teste de "a imagem chega", e invalidaria o
        cache de prompt de TODO request do projeto — o cache hit é 50x
        mais barato, então isto é dinheiro, não estilo.
        """
        out = OpenAICompatibleProvider._encode_messages(
            OpenAICompatibleProvider, [Message(role="user", content="oi")]
        )
        assert out[0]["content"] == "oi"
        assert isinstance(out[0]["content"], str)

    def test_imagem_vira_data_url_com_o_tipo_certo(self) -> None:
        """Sabotagem: fixar `image/png` no prefixo da data URL.

        O provedor detecta pelo conteúdo, então um prefixo errado
        funcionaria às vezes — e falharia só com JPEG, que é justamente
        o que sai de uma câmera.
        """
        dados = b"\xff\xd8\xff\xe0conteudo-falso-de-jpeg"
        msg = Message(
            role="user",
            content=(
                TextPart(text="o que é isto?"),
                ImagePart(data=dados, media_type="image/jpeg"),
            ),
        )
        out = OpenAICompatibleProvider._encode_messages(OpenAICompatibleProvider, [msg])
        partes = out[0]["content"]
        assert isinstance(partes, list)
        assert partes[0] == {"type": "text", "text": "o que é isto?"}
        url = partes[1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        # E os bytes voltam inteiros: um base64 de `str(dados)` ou com
        # padding comido passaria no `startswith` acima.
        assert base64.b64decode(url.split(",", 1)[1]) == dados

    def test_ordem_das_partes_e_preservada(self) -> None:
        """Sabotagem: montar as partes com as imagens antes do texto.

        "Compare estas duas" só faz sentido com a ordem que a pessoa
        escreveu; embaralhar muda a pergunta sem erro nenhum.
        """
        msg = Message(
            role="user",
            content=(
                TextPart(text="primeiro"),
                ImagePart(data=png_bytes(), media_type="image/png"),
                TextPart(text="ultimo"),
            ),
        )
        out = OpenAICompatibleProvider._encode_messages(OpenAICompatibleProvider, [msg])
        tipos = [p["type"] for p in out[0]["content"]]
        assert tipos == ["text", "image_url", "text"]
        assert out[0]["content"][2]["text"] == "ultimo"
