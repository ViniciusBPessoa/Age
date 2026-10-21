"""Extração de texto de PDF anexado.

Os PDFs são montados à mão: gerar com uma biblioteca traria dependência
só para o teste, e um PDF é texto estruturado — cabe aqui.

Cada teste traz a sabotagem que precisa derrubá-lo.
"""

from __future__ import annotations

import io

import pytest

from app.services.pdf_recebido import (
    MAXIMO_DE_PAGINAS,
    MINIMO_DE_CARACTERES,
    PdfRecusado,
    e_pdf,
    extrair,
)


def _montar(objetos: dict[int, bytes], raiz: int = 1) -> bytes:
    """Escreve um PDF válido a partir dos objetos numerados."""
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objetos):
        offsets[num] = buf.tell()
        buf.write(f"{num} 0 obj\n".encode() + objetos[num] + b"\nendobj\n")
    xref = buf.tell()
    maior = max(offsets)
    buf.write(f"xref\n0 {maior + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for i in range(1, maior + 1):
        buf.write(f"{offsets.get(i, 0):010d} 00000 n \n".encode())
    buf.write(
        f"trailer\n<< /Size {maior + 1} /Root {raiz} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF".encode()
    )
    return buf.getvalue()


def pdf_com_texto(paginas: int = 1, linhas: int = 30) -> bytes:
    objetos: dict[int, bytes] = {}
    conteudos_ini = 3
    kids = []
    for p in range(paginas):
        corpo = "\n".join(
            f"BT /F1 11 Tf 40 {760 - i * 20} Td "
            f"(Pagina {p + 1} linha {i + 1}: texto extraivel do documento.) Tj ET"
            for i in range(linhas)
        ).encode("latin-1")
        objetos[conteudos_ini + p] = (
            b"<< /Length "
            + str(len(corpo)).encode()
            + b" >>\nstream\n"
            + corpo
            + b"\nendstream"
        )
    fonte = conteudos_ini + paginas
    objetos[fonte] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for p in range(paginas):
        pid = fonte + 1 + p
        kids.append(pid)
        objetos[pid] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {fonte} 0 R >> >> "
            f"/Contents {conteudos_ini + p} 0 R >>".encode()
        )
    objetos[2] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
        f"/Count {paginas} >>".encode()
    )
    objetos[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    return _montar(objetos)


def pdf_sem_texto() -> bytes:
    """O escaneado: página válida, nenhum operador de texto."""
    corpo = b"0.5 0.5 0.5 rg 100 100 400 600 re f"
    return _montar(
        {
            3: b"<< /Length "
            + str(len(corpo)).encode()
            + b" >>\nstream\n"
            + corpo
            + b"\nendstream",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 3 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
        }
    )


class TestReconhecimento:
    def test_reconhece_pdf_pela_assinatura(self) -> None:
        """Sabotagem: olhar a extensão do nome.

        O nome vem do cliente. Um `.pdf` com bytes de executável não
        pode chegar ao `pypdf`.
        """
        assert e_pdf(b"%PDF-1.7\nresto") is True
        assert e_pdf(b"%PDF-") is True

    @pytest.mark.parametrize(
        "dados",
        [
            b"",
            b"PDF-1.4",  # falta o %
            b"\x89PNG\r\n\x1a\n",  # imagem
            b"MZ\x90\x00",  # executável
            b"  %PDF-1.4",  # lixo antes do cabeçalho
        ],
    )
    def test_recusa_o_que_nao_e_pdf(self, dados: bytes) -> None:
        """Sabotagem: procurar `%PDF-` em qualquer posição.

        Leitores toleram lixo antes do cabeçalho, mas aceitar isso
        abriria a porta para arquivo com assinatura dupla — um PNG
        válido que também "contém" `%PDF-` adiante.
        """
        assert e_pdf(dados) is False


class TestExtracao:
    def test_extrai_o_texto_de_uma_pagina(self) -> None:
        """Sabotagem: devolver string vazia, ou só a primeira linha."""
        r = extrair(pdf_com_texto(1))
        assert "texto extraivel do documento" in r.texto
        assert r.paginas == 1
        assert r.truncado is False

    def test_junta_todas_as_paginas(self) -> None:
        """Sabotagem: ler só `pages[0]`.

        Passaria no teste de cima e entregaria 1/10 de um documento ao
        modelo, que responderia com confiança sobre o que não leu.
        """
        r = extrair(pdf_com_texto(5))
        assert r.paginas == 5
        assert "Pagina 1 " in r.texto
        assert "Pagina 5 " in r.texto

    def test_corta_no_teto_de_paginas_e_avisa(self) -> None:
        """Sabotagem: ler tudo sem teto, ou cortar sem marcar `truncado`.

        Medido: 40 páginas dão ~20.600 tokens, MAIS que o prompt
        inteiro do Age — e o histórico é relido a cada mensagem
        seguinte, então um PDF grande encarece a conversa toda. Cortar
        é necessário; cortar SEM AVISAR faria o modelo responder sobre
        metade do documento como se fosse o todo.
        """
        r = extrair(pdf_com_texto(MAXIMO_DE_PAGINAS + 10, linhas=3))
        assert r.paginas == MAXIMO_DE_PAGINAS + 10
        assert r.paginas_lidas == MAXIMO_DE_PAGINAS
        assert r.truncado is True

    def test_limpa_o_ruido_de_layout(self) -> None:
        """Sabotagem: remover a chamada a `_limpar`.

        PDF guarda posição, não parágrafo: a extração devolve quebras a
        cada linha visual e páginas separadas por vazios. Isso é token
        gasto com estrutura que não significa nada.

        Afirmo sobre `_limpar` DIRETO, com uma entrada que tem o ruído.
        A versão que escrevi primeiro passava pelo `extrair` e continuava
        verde com a chamada removida: os PDFs que este arquivo monta não
        produzem o ruído que a função existe para tirar, então o teste
        não testava nada. Peguei na sabotagem.
        """
        from app.services.pdf_recebido import _limpar

        sujo = "\n\nlinha um   \n\n\n\nlinha dois\t\n\n\n"
        limpo = _limpar(sujo)

        assert limpo.startswith("linha um")
        assert limpo.endswith("linha dois")
        assert "\n\n\n" not in limpo
        assert "   \n" not in limpo

    def test_o_texto_extraido_passa_pela_limpeza(self) -> None:
        """A outra metade: `extrair` REALMENTE chama `_limpar`.

        Sabotagem: a mesma. Sem esta asserção, a limpeza poderia estar
        perfeita e nunca ser usada — que foi o estado em que o arquivo
        ficou até a sabotagem mostrar.
        """
        import inspect

        from app.services import pdf_recebido

        corpo = inspect.getsource(pdf_recebido.extrair)
        assert "_limpar(" in corpo


class TestRecusas:
    def test_pdf_escaneado_e_recusado_com_instrucao(self) -> None:
        """Sabotagem: devolver texto vazio como SUCESSO.

        É o caso mais comum de "anexei e o Age não entendeu", e falhar
        calado nele é o pior resultado: o anexo é aceito, gravado, e o
        modelo responde sem ter visto nada. A frase precisa dizer o que
        fazer — mandar um print, que o modelo lê como imagem.
        """
        with pytest.raises(PdfRecusado) as e:
            extrair(pdf_sem_texto(), nome="scan.pdf")
        assert "escaneado" in e.value.motivo
        assert "print" in e.value.motivo
        # E o nome do arquivo, para quem anexou vários saber qual falhou.
        assert "scan.pdf" in e.value.motivo

    def test_texto_insuficiente_e_tratado_como_sem_texto(self) -> None:
        """Sabotagem: exigir apenas `len(texto) > 0`.

        Um PDF escaneado às vezes devolve alguns caracteres perdidos
        (número de página, cabeçalho). Aceitá-los faria o modelo
        responder sobre nada — com a aparência de ter lido.
        """
        # Uma linha curta de verdade: ~35 caracteres, abaixo do
        # minimo. O `[:0] or ...` que eu tinha escrito aqui caia sempre
        # no PDF sem texto e nao testava o limiar.
        # O limiar precisa ser alto o bastante para descartar ruido
        # de layout (numero de pagina solto), nao so o vazio.
        assert MINIMO_DE_CARACTERES >= 20

        curto = pdf_com_texto(1, linhas=1)
        with pytest.raises(PdfRecusado) as e:
            extrair(curto, nome="quase-vazio.pdf")
        assert "escaneado" in e.value.motivo

    def test_bytes_que_nao_sao_pdf(self) -> None:
        """Sabotagem: deixar o `PdfReader` levantar direto.

        "Stream has ended unexpectedly" não diz nada a quem anexou.
        """
        with pytest.raises(PdfRecusado) as e:
            extrair(b"isto nao e um pdf")
        assert "PDF" in e.value.motivo

    def test_pdf_truncado(self) -> None:
        """Sabotagem: a mesma — e este é o caso do upload interrompido.

        A frase precisa sugerir a causa ("enviado pela metade"), senão
        a pessoa reenvia o mesmo arquivo achando que é o servidor.
        """
        with pytest.raises(PdfRecusado) as e:
            extrair(pdf_com_texto(1)[:300])
        assert "corrompido" in e.value.motivo or "metade" in e.value.motivo
