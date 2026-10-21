"""Upload de imagem para anexar a uma mensagem (§12).

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

router = APIRouter(prefix="/attachments", tags=["attachments"])


class AttachmentOut(BaseModel):
    id: UUID
    filename: str
    media_type: str
    size_bytes: int


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
    # chegou. Leio tudo porque o limite é 5 MB — para limite grande,
    # isto teria de ser leitura em pedaços com corte no meio.
    dados = await file.read()

    try:
        imagem = validar(dados)
    except ImagemRecusada as e:
        # 422 e não 400: o pedido está bem formado, o conteúdo é que
        # não serve. E `motivo` é escrito para quem está olhando a tela.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "imagem_recusada", "message": e.motivo},
        ) from e

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
            filename=(file.filename or "imagem")[:200],
            media_type=imagem.media_type,
            size_bytes=imagem.tamanho,
            sha256=imagem.sha256,
            data=imagem.dados,
        )
    )
    await db.flush()

    return AttachmentOut(
        id=attachment_id,
        filename=(file.filename or "imagem")[:200],
        media_type=imagem.media_type,
        size_bytes=imagem.tamanho,
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
