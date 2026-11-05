"""Upload de anexo para uma mensagem: imagem ou PDF (§5.1.1).

## Por que uma rota separada, e não o corpo do POST da mensagem

O anexo nasce ANTES da mensagem. A pessoa escolhe a imagem enquanto
ainda está escrevendo, vê a miniatura, pode trocá-la ou remover — e só
depois manda. Embutir os bytes no POST do envio obrigaria a esperar o
upload no clique de enviar, com a interface parada, e perderia tudo se
o envio falhasse.

Então: `POST /attachments` devolve um id, e `POST /messages` recebe a
lista de ids. Anexo que nunca é referenciado fica órfão, e é por isso
que a migration 0013 tem índice parcial para achá-lo.

## O limite de 5 MB e o BFF

O BFF (`app/api/[...path]/route.ts`) bufferiza todo POST em memória para
poder repetir o request após renovar o token, e o cabeçalho de lá avisa
que isso impõe "um limite de tamanho implícito". Com 5 MB o buffer é
aceitável — é da ordem de uma foto de celular, e o Node aguenta sem
suar. Por isso NÃO abri caminho de upload paralelo: seria infraestrutura
nova para resolver um problema que este limite já resolve.

Se um dia o limite subir para vídeo ou PDF de centenas de páginas, o
caminho certo é upload direto para object storage com URL assinada — e
aí o BFF sai da frente por completo, em vez de ser remendado.

## PDF vira TEXTO, e a tela diz isso

O modelo não aceita PDF. O texto é extraído aqui com `pypdf` e enviado
como texto; os bytes originais ficam guardados para a pessoa reabrir o
que mandou. PDF escaneado (foto das páginas) não tem texto para
extrair, e é recusado com uma frase que explica o que fazer — em vez de
ser aceito e silenciosamente não chegar ao modelo.

## O que é validado, e onde

O tipo vem dos BYTES (`services/imagem_recebida.py`), nunca do
`Content-Type` que o cliente mandou nem da extensão do arquivo. O
tamanho é checado antes do formato, porque é o que a pessoa pode
resolver. E a constraint da migration 0013 repete os dois limites no
banco: a rota dá a frase legível, a constraint garante a invariante.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models import MessageAttachment
from app.services.imagem_recebida import (
    MAXIMO_POR_MENSAGEM,
    TAMANHO_MAXIMO,
    ImagemRecusada,
    validar,
)
from app.services.pdf_recebido import PdfRecusado, extrair

router = APIRouter(prefix="/attachments", tags=["attachments"])


class AttachmentOut(BaseModel):
    id: UUID
    filename: str
    media_type: str
    size_bytes: int
    # Só em PDF. A tela mostra "12 páginas" e avisa quando o documento
    # foi cortado no teto — um anexo que virou outra coisa precisa
    # dizer isso, senão a pessoa supõe que o modelo viu o arquivo.
    page_count: int | None = None
    pages_read: int | None = None


@router.post(
    "",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Envia uma imagem para anexar a uma mensagem",
)
async def upload_attachment(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> AttachmentOut:
    # `file.size` vem do cliente e pode mentir; o que vale é o que
    # chegou. Lê no máximo um byte além do limite: é o bastante para
    # `validar` recusar com a frase de tamanho, e um envio de 2 GB não
    # vira 2 GB na memória do processo antes de ser recusado.
    dados = await file.read(TAMANHO_MAXIMO + 1)

    try:
        anexo = validar(dados)
    except ImagemRecusada as e:
        # 422 e não 400: o pedido está bem formado, o conteúdo é que
        # não serve. E `motivo` é escrito para quem está olhando a tela.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "imagem_recusada", "message": e.motivo},
        ) from e

    nome = (file.filename or ("documento.pdf" if anexo.e_pdf else "imagem"))[:200]

    # O texto do PDF sai AQUI, não na hora de montar o prompt.
    #
    # PDF escaneado não tem texto nenhum, e descobrir isso no worker
    # faria a pessoa esperar um turno inteiro — já cobrado — para ouvir
    # "não entendi o anexo". Extraindo no upload, a recusa é imediata e
    # o arquivo ainda está na tela.
    texto: str | None = None
    paginas: int | None = None
    lidas: int | None = None
    if anexo.e_pdf:
        try:
            pdf = extrair(anexo.dados, nome=nome)
        except PdfRecusado as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "pdf_recusado", "message": e.motivo},
            ) from e
        texto, paginas, lidas = pdf.texto, pdf.paginas, pdf.paginas_lidas

    attachment_id = uuid4()
    db.add(
        MessageAttachment(
            id=attachment_id,
            user_id=user.id,
            # Nasce sem mensagem: o envio é que amarra.
            message_id=None,
            conversation_id=None,
            # Nome só para exibir. Truncado porque é texto de origem
            # externa e a coluna não tem por que receber 4 KB de nome.
            filename=nome,
            media_type=anexo.media_type,
            size_bytes=anexo.tamanho,
            sha256=anexo.sha256,
            data=anexo.dados,
            extracted_text=texto,
            page_count=paginas,
        )
    )
    await db.flush()

    return AttachmentOut(
        id=attachment_id,
        filename=nome,
        media_type=anexo.media_type,
        size_bytes=anexo.tamanho,
        page_count=paginas,
        pages_read=lidas,
    )


async def vincular_anexos(
    db: DbSession,
    *,
    user_id: UUID,
    attachment_ids: list[UUID],
    message_id: UUID,
    conversation_id: UUID,
) -> None:
    """Amarra anexos já enviados a uma mensagem que acabou de nascer.

    Chamado pelo envio da mensagem, dentro da MESMA transação: se o
    envio falhar, os anexos continuam órfãos e reutilizáveis, em vez de
    apontarem para uma mensagem que não existe.

    Recusa id que não é da conta (RLS já filtraria, mas o erro explícito
    diz o que aconteceu), id já usado em outra mensagem, e lista acima
    do limite. Um id repetido na mesma lista conta uma vez.
    """
    if not attachment_ids:
        return

    # `dict.fromkeys` preserva a ordem e remove repetido: a ordem é o
    # que a pessoa escolheu, e "compare a primeira com a segunda" só
    # faz sentido se ela sobreviver.
    unicos = list(dict.fromkeys(attachment_ids))
    if len(unicos) > MAXIMO_POR_MENSAGEM:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "anexos_demais",
                "message": f"No máximo {MAXIMO_POR_MENSAGEM} imagens por mensagem.",
            },
        )

    linhas = (
        await db.execute(
            select(MessageAttachment).where(
                MessageAttachment.id.in_(unicos),
                MessageAttachment.user_id == user_id,
            )
        )
    ).scalars()
    por_id = {linha.id: linha for linha in linhas}

    for anexo_id in unicos:
        anexo = por_id.get(anexo_id)
        if anexo is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "anexo_desconhecido",
                    "message": "Um dos anexos não existe mais. Envie a imagem de novo.",
                },
            )
        if anexo.message_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "anexo_ja_usado",
                    "message": "Este anexo já foi enviado em outra mensagem.",
                },
            )

    for posicao, anexo_id in enumerate(unicos):
        anexo = por_id[anexo_id]
        anexo.message_id = message_id
        anexo.conversation_id = conversation_id
        anexo.position = posicao


# Exportado para a rota de envio montar o limite na mensagem de erro
# sem reimportar o serviço.
__all__ = [
    "AttachmentOut",
    "MAXIMO_POR_MENSAGEM",
    "TAMANHO_MAXIMO",
    "router",
    "vincular_anexos",
]


@router.get(
    "/{attachment_id}/content",
    summary="Os bytes de um anexo, para a tela mostrar a imagem",
    response_class=Response,
)
async def attachment_content(
    attachment_id: UUID,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    """Serve a imagem.

    Rota separada da listagem de mensagens de propósito: 4 fotos de
    5 MB viram ~27 MB em base64, e a listagem é chamada no `done` de
    TODO turno. Aqui cada imagem é uma requisição que o navegador
    cacheia como qualquer outra.

    A segurança é o `WHERE user_id`: o RLS já filtra, e a condição
    explícita faz o 404 vir no lugar de um 500 confuso. Um id de outra
    conta é 404, não 403 — "este anexo existe mas não é seu" é
    informação que não precisa ser dada.
    """
    linha = (
        await db.execute(
            select(MessageAttachment).where(
                MessageAttachment.id == attachment_id,
                MessageAttachment.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if linha is None or linha.data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "anexo_nao_encontrado", "message": "Anexo não existe."},
        )

    return Response(
        content=bytes(linha.data),
        media_type=linha.media_type,
        headers={
            # `private`: a resposta depende do cookie de sessão, e um
            # cache compartilhado guardando isto entregaria a imagem de
            # uma conta para outra. O conteúdo é imutável — o id nunca
            # aponta para bytes diferentes —, então `immutable` poupa
            # revalidação enquanto a aba vive.
            "Cache-Control": "private, max-age=86400, immutable",
            # Nunca inline: um SVG malicioso servido inline executaria
            # script na origem da API. Não aceitamos SVG hoje, e este
            # header garante que aceitar um dia não vire um buraco.
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
        },
    )
