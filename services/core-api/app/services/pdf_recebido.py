"""Extração de texto de PDF anexado.

O modelo **não aceita PDF** — só imagem (§5.1.1). O texto é extraído
aqui e enviado como texto, e a tela diz que foi isso que aconteceu: um
anexo que silenciosamente vira outra coisa é a classe de surpresa que
faz a pessoa não confiar no resultado.

## Por que aqui, e não no worker

O erro precisa chegar **antes** do turno. PDF escaneado não tem texto
nenhum para extrair; descobrir isso no worker faria a pessoa anexar,
escrever a pergunta, mandar, esperar — e receber "não entendi o anexo"
de um turno já cobrado. Extraindo no upload, a recusa é imediata e o
arquivo ainda está na tela.

O custo é que a extração acontece no caminho da requisição. Medido com
`pypdf` 6.21: 40 páginas em alguns décimos de segundo, o que cabe num
POST. Se um dia entrar OCR, aí sim vai para a fila — OCR é ordem de
grandeza diferente.

## Os números que decidiram o teto de páginas

Medido, com ~4 caracteres por token:

| PDF | Texto | Tokens | Custo |
|---|---|---|---|
| 1 página | 2.030 chars | ~507 | US$ 0,00008 |
| 10 páginas | 20.349 chars | ~5.087 | US$ 0,00076 |
| 40 páginas | 82.479 chars | ~20.619 | US$ 0,00309 |

Um PDF de 40 páginas gera **mais tokens que o prompt inteiro do Age**
(~12.850, system prompt + as 31 ferramentas). E o custo não é de um
turno: o histórico é relido a cada mensagem seguinte da mesma conversa,
então um PDF grande encarece a conversa inteira, não só a pergunta que
o trouxe.

Por isso o corte em 50 páginas, com aviso — e não um truncamento mudo,
que faria o modelo responder sobre metade do documento como se fosse o
todo.

## O que NÃO é feito aqui

**OCR.** PDF escaneado (foto de página, sem camada de texto) devolve
zero caracteres, e isto recusa com uma frase que explica o que fazer.
Fazer OCR exigiria Tesseract ou um serviço pago, e nenhum dos dois cabe
num upload síncrono.

**Imagens embutidas.** Um PDF pode ter figuras que importam para a
pergunta; só o texto sai daqui. A alternativa seria rasterizar as
páginas e mandar como imagem — o que custa até 994 tokens por página e
precisaria de dependência nativa.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

# Acima disto, o custo deixa de ser desprezível: ~25.700 tokens, mais
# que o prompt do Age inteiro, cobrados de novo a cada mensagem
# seguinte da conversa.
MAXIMO_DE_PAGINAS = 50

# Abaixo disto, o "texto" extraído é ruído de layout (números de página,
# cabeçalhos soltos) e não conteúdo. Um PDF escaneado às vezes devolve
# alguns caracteres perdidos em vez de zero, e tratá-los como sucesso
# faria o modelo responder sobre nada.
MINIMO_DE_CARACTERES = 50


@dataclass(frozen=True)
class PdfExtraido:
    texto: str
    paginas: int
    paginas_lidas: int

    @property
    def truncado(self) -> bool:
        return self.paginas_lidas < self.paginas


class PdfRecusado(Exception):
    """Recusa com motivo legível, escrito para quem anexou o arquivo."""

    def __init__(self, motivo: str) -> None:
        self.motivo = motivo
        super().__init__(motivo)


def e_pdf(dados: bytes) -> bool:
    """Assinatura de PDF, pelos BYTES.

    `%PDF-` nos primeiros bytes. Alguns geradores põem lixo antes do
    cabeçalho, e leitores toleram — mas aceitar isso aqui abriria a
    porta para arquivo com assinatura dupla, então a checagem é estrita
    e um PDF malformado recusa com uma frase clara.
    """
    return dados[:5] == b"%PDF-"


def _limpar(texto: str) -> str:
    """Tira o ruído que a extração sempre traz.

    PDF guarda posição, não parágrafo: a extração devolve quebras de
    linha a cada linha visual e páginas separadas por vazios. Mandar
    isso cru gasta tokens com estrutura que não significa nada.
    """
    # Espaço no fim de linha, e mais de duas quebras seguidas.
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def extrair(dados: bytes, *, nome: str = "documento.pdf") -> PdfExtraido:
    """O texto do PDF, ou `PdfRecusado` com o motivo.

    Nunca devolve texto vazio como sucesso: PDF escaneado é o caso mais
    comum de "anexei e o Age não entendeu", e falhar calado nele é o
    pior resultado possível.
    """
    if not e_pdf(dados):
        raise PdfRecusado("Este arquivo não parece um PDF.")

    # Importado aqui, não no topo: `pypdf` só é necessário quando um PDF
    # realmente chega, e o módulo é carregado em todo import da app.
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError, PdfStreamError

    try:
        leitor = PdfReader(io.BytesIO(dados))
    except (PdfReadError, PdfStreamError) as e:
        # A mensagem do pypdf ("Stream has ended unexpectedly") não diz
        # nada a quem anexou; a nossa diz o que provavelmente aconteceu.
        raise PdfRecusado(
            "Não consegui ler este PDF. Ele pode estar corrompido ou "
            "ter sido enviado pela metade."
        ) from e

    if getattr(leitor, "is_encrypted", False):
        # Tentar senha vazia funciona em muitos PDFs "protegidos" só
        # contra impressão. Se falhar, é cifrado de verdade.
        try:
            if leitor.decrypt("") == 0:  # type: ignore[union-attr]
                raise PdfRecusado(
                    "Este PDF está protegido por senha. Remova a proteção "
                    "e envie de novo."
                )
        except PdfRecusado:
            raise
        except Exception as e:
            raise PdfRecusado(
                "Este PDF está protegido por senha. Remova a proteção e envie de novo."
            ) from e

    try:
        total = len(leitor.pages)
    except (PdfReadError, PdfStreamError) as e:
        raise PdfRecusado("Não consegui ler as páginas deste PDF.") from e

    if total == 0:
        raise PdfRecusado("Este PDF não tem páginas.")

    lidas = min(total, MAXIMO_DE_PAGINAS)
    partes: list[str] = []
    for indice in range(lidas):
        try:
            partes.append(leitor.pages[indice].extract_text() or "")
        except Exception:
            # Uma página ilegível não derruba o documento: PDFs reais
            # têm páginas com fontes exóticas, e perder uma é melhor
            # que recusar as outras 49.
            partes.append("")

    texto = _limpar("\n\n".join(p for p in partes if p.strip()))

    if len(texto) < MINIMO_DE_CARACTERES:
        raise PdfRecusado(
            f"Não achei texto em “{nome}”. Se for um PDF escaneado (foto "
            "das páginas), o texto não está lá para ser lido — mande um "
            "print da parte que importa, que eu leio a imagem."
        )

    return PdfExtraido(texto=texto, paginas=total, paginas_lidas=lidas)
