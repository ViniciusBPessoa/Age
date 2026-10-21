"""Rotas de conversa e de turno.

ARQUITETURA.md §6.2 (multi-chat) e §7.1 (streaming em três serviços).

O desenho de `POST` para criar o turno e `GET` separado para o stream não
é contorno: com o orquestrador em outro processo, o turno existe como
recurso antes de o stream abrir — e aí `EventSource` nativo volta a
servir, porque ele só faz GET.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import httpx
from age_contracts import CONTRACT_VERSION, JobName
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sse_starlette.sse import EventSourceResponse

from app.api.attachments import vincular_anexos
from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import enforce
from app.core.redis import get_arq, get_redis, turn_cancel_key, turn_stream_key
from app.core.security import create_service_token
from app.core.tracing import current_trace_id
from app.models import Conversation, Message
from app.services.plan_store import promote_drafts
from app.services.turn_sync import (
    apply_pending_titles,
    apply_title_suggestion,
    drain_turn_outputs,
)

log = logging.getLogger("core-api.conversations")
router = APIRouter(tags=["conversations"])


# ---------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------
class ConversationOut(BaseModel):
    id: UUID
    title: str | None
    title_is_auto: bool
    provider_key: str | None
    model: str | None
    # Nullable: `None` significa "herda da conta" (§5.7.2). Um default
    # não-nulo faria a camada 2 vencer a camada 1 por acidente.
    target_default: str | None
    pinned_at: datetime | None
    archived_at: datetime | None
    last_message_at: datetime | None
    message_count: int
    created_at: datetime
    # §8.1: quantos eventos e tarefas esta conversa colocou na vida da
    # pessoa. É o que diferencia "conversa que resolveu algo" de
    # "conversa que não foi pra frente" ao olhar a lista meses depois —
    # sem isto a sidebar é uma pilha de títulos.
    applied_events: int = 0
    applied_tasks: int = 0


class ConversationList(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None = None


class CreateConversation(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    provider_key: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=100)


class UpdateConversation(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    provider_key: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=100)
    target_default: Literal["event", "task", "both", "ask"] | None = None


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str | None
    provider_key: str | None
    model: str | None
    # Os tokens já estavam no banco desde o M1 e eram descartados aqui
    # na serialização. O custo é do PAR (pergunta + resposta) e fica na
    # resposta, porque o provedor cobra o prompt inteiro — que já inclui
    # a pergunta. Mensagem do usuário vem com os três em `null`.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost_usd: float | None = None
    created_at: datetime


class MessageList(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None = None


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    # Idempotência no envio: duplo-clique não custa dois turnos (§5.1).
    client_turn_id: UUID | None = None
    # Ids devolvidos por POST /attachments. Vazio no caso comum.
    #
    # `content` segue obrigatorio mesmo com anexo: imagem sem
    # pergunta faz o modelo adivinhar o que se quer dela, e a
    # resposta vira descricao generica. A tela sugere uma pergunta
    # em vez de deixar enviar vazio.
    #
    # `max_length` repete o limite de `MAXIMO_POR_MENSAGEM`: aqui o
    # pydantic recusa antes de abrir transacao, e `vincular_anexos`
    # recusa de novo porque e ele que fala com o banco.
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=4)


class TurnAccepted(BaseModel):
    turn_id: UUID
    message_id: UUID
    stream_url: str


# ---------------------------------------------------------------
# CRUD de conversa
# ---------------------------------------------------------------
@router.get("/conversations")
async def list_conversations(
    user: CurrentUser,
    db: DbSession,
    filter: Literal["active", "archived", "trash"] = "active",
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> ConversationList:
    """Lista para a sidebar (§8.1).

    Paginação por cursor, não offset: a lista muda enquanto é paginada
    (§7.0).
    """
    # O worker grava a resposta em `ai.turn_outputs` porque não pode
    # escrever em `core` (§2.3.2). Drenar na leitura garante que a
    # sidebar nunca mostre contagem defasada.
    await drain_turn_outputs(db, user.id)
    # Pelo mesmo motivo, o titulo sugerido precisa ser aplicado aqui: a
    # versao por conversa so roda ao abrir uma, e quem acabou de mandar
    # a primeira mensagem esta olhando justamente para a lista.
    await apply_pending_titles(db, user.id)

    stmt = select(Conversation).where(Conversation.user_id == user.id)

    match filter:
        case "active":
            stmt = stmt.where(
                Conversation.deleted_at.is_(None),
                Conversation.archived_at.is_(None),
            )
        case "archived":
            stmt = stmt.where(
                Conversation.deleted_at.is_(None),
                Conversation.archived_at.is_not(None),
            )
        case "trash":
            stmt = stmt.where(Conversation.deleted_at.is_not(None))

    if q:
        # Busca por título OU conteúdo das mensagens (§8.1).
        matching = select(Message.conversation_id).where(
            Message.user_id == user.id,
            text(
                "to_tsvector('simple', coalesce(core.messages.content, '')) "
                "@@ plainto_tsquery('simple', :q)"
            ),
        )
        stmt = stmt.where(
            Conversation.title.ilike(f"%{q}%") | Conversation.id.in_(matching)
        ).params(q=q)

    if cursor:
        try:
            stmt = stmt.where(Conversation.created_at < datetime.fromisoformat(cursor))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {"code": "invalid_cursor", "message": "Cursor inválido."}
                },
            ) from exc

    # Fixadas primeiro, depois por recência.
    stmt = stmt.order_by(
        Conversation.pinned_at.desc().nullslast(),
        Conversation.last_message_at.desc().nullslast(),
        Conversation.created_at.desc(),
    ).limit(limit + 1)

    rows = list((await db.execute(stmt)).scalars())
    has_more = len(rows) > limit
    page = rows[:limit]

    counts = await _applied_counts(db, user.id, [c.id for c in page])

    items = []
    for conversation in page:
        out = ConversationOut.model_validate(conversation, from_attributes=True)
        events, tasks = counts.get(conversation.id, (0, 0))
        items.append(
            out.model_copy(update={"applied_events": events, "applied_tasks": tasks})
        )

    return ConversationList(
        items=items,
        next_cursor=page[-1].created_at.isoformat() if has_more and page else None,
    )


async def _applied_counts(
    db: DbSession, user_id: UUID, conversation_ids: list[UUID]
) -> dict[UUID, tuple[int, int]]:
    """Itens de plano APLICADOS por conversa, separados por destino.

    Uma query agregada para a página inteira, e não uma por conversa:
    a sidebar é a tela mais aberta do produto, e 50 conversas dariam 50
    consultas em cada abertura.

    Conta `status = 'applied'` no item, não no plano: aprovação é
    granular (§5.8), então um plano `partially_applied` tem itens
    aplicados e itens rejeitados, e só os primeiros entraram na vida da
    pessoa. Contar pelo plano inflaria o número — e um contador que
    exagera o que o produto fez é pior que contador nenhum.

    O id vai como JSON e volta via `json_array_elements_text` porque
    asyncpg recusa literal de array de Postgres num parâmetro tipado
    (mesma lição do M4, em `reorder` e `list_tasks`).
    """
    if not conversation_ids:
        return {}

    rows = await db.execute(
        text(
            """
            SELECT p.conversation_id,
                   count(*) FILTER (WHERE i.target = 'event') AS events,
                   count(*) FILTER (WHERE i.target = 'task')  AS tasks
            FROM core.plan_items i
            JOIN core.plans p ON p.id = i.plan_id
            WHERE i.user_id = :uid
              AND i.status = 'applied'
              AND p.conversation_id IN (
                    SELECT value::uuid
                    FROM json_array_elements_text(CAST(:ids AS json))
                  )
            GROUP BY p.conversation_id
            """
        ),
        {
            "uid": str(user_id),
            "ids": json.dumps([str(cid) for cid in conversation_ids]),
        },
    )
    return {row[0]: (row[1], row[2]) for row in rows.all()}


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversation, user: CurrentUser, db: DbSession
) -> ConversationOut:
    conversation_id = uuid4()
    db.add(
        Conversation(
            id=conversation_id,
            user_id=user.id,
            title=body.title,
            title_is_auto=body.title is None,
            provider_key=body.provider_key,
            model=body.model,
        )
    )
    await db.flush()
    row = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one()
    return ConversationOut.model_validate(row, from_attributes=True)


async def _load_conversation(
    db: DbSession, user: CurrentUser, conversation_id: UUID, *, allow_deleted=False
) -> Conversation:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user.id
    )
    if not allow_deleted:
        stmt = stmt.where(Conversation.deleted_at.is_(None))

    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": "Conversa não encontrada.",
                }
            },
        )
    return row


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID, user: CurrentUser, db: DbSession
) -> ConversationOut:
    await _load_conversation(db, user, conversation_id)
    await drain_turn_outputs(db, user.id, conversation_id)
    await apply_title_suggestion(db, user.id, conversation_id)

    row = await _load_conversation(db, user, conversation_id)
    return ConversationOut.model_validate(row, from_attributes=True)


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: UUID,
    body: UpdateConversation,
    user: CurrentUser,
    db: DbSession,
) -> ConversationOut:
    await _load_conversation(db, user, conversation_id)
    values = body.model_dump(exclude_none=True)

    # Renomear manualmente desliga o título automático para sempre
    # (§6.2.2): o gerado nunca sobrescreve escolha humana.
    if "title" in values:
        values["title_is_auto"] = False

    if values:
        values["updated_at"] = datetime.now(UTC)
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(**values)
        )
    row = await _load_conversation(db, user, conversation_id)
    return ConversationOut.model_validate(row, from_attributes=True)


@router.post("/conversations/{conversation_id}/archive", status_code=204)
async def archive(conversation_id: UUID, user: CurrentUser, db: DbSession) -> None:
    await _load_conversation(db, user, conversation_id)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(archived_at=datetime.now(UTC))
    )


@router.post("/conversations/{conversation_id}/unarchive", status_code=204)
async def unarchive(conversation_id: UUID, user: CurrentUser, db: DbSession) -> None:
    await _load_conversation(db, user, conversation_id)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(archived_at=None)
    )


@router.post("/conversations/{conversation_id}/pin", status_code=204)
async def pin(conversation_id: UUID, user: CurrentUser, db: DbSession) -> None:
    await _load_conversation(db, user, conversation_id)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(pinned_at=datetime.now(UTC))
    )


@router.post("/conversations/{conversation_id}/unpin", status_code=204)
async def unpin(conversation_id: UUID, user: CurrentUser, db: DbSession) -> None:
    await _load_conversation(db, user, conversation_id)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(pinned_at=None)
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID, user: CurrentUser, db: DbSession
) -> None:
    """Soft delete, com janela de 30 dias (§6.2.4).

    Não apaga na hora por três razões: arrepender-se é comum; um turno
    pode estar rodando no worker e violaria FK; e o audit_log ficaria
    apontando para nada.

    O que esta rota NÃO faz: tocar eventos, tarefas ou planos que a
    conversa criou. Eles foram aprovados pelo usuário (§6.2.3).
    """
    await _load_conversation(db, user, conversation_id)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(deleted_at=datetime.now(UTC), pinned_at=None)
    )


@router.post("/conversations/{conversation_id}/restore", status_code=204)
async def restore(conversation_id: UUID, user: CurrentUser, db: DbSession) -> None:
    await _load_conversation(db, user, conversation_id, allow_deleted=True)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(deleted_at=None)
    )


@router.post("/conversations/{conversation_id}/fork", status_code=201)
async def fork_conversation(
    conversation_id: UUID,
    user: CurrentUser,
    db: DbSession,
    up_to_message_id: UUID | None = None,
) -> ConversationOut:
    """Ramifica a partir de uma mensagem (§8.1).

    Equivale a editar mensagem antiga nos outros produtos, mas preserva
    as duas versões em vez de sobrescrever — útil quando um plano foi
    para um lado e você quer tentar outro.
    """
    source = await _load_conversation(db, user, conversation_id)

    new_id = uuid4()
    db.add(
        Conversation(
            id=new_id,
            user_id=user.id,
            title=f"{source.title or 'Conversa'} (ramo)",
            title_is_auto=False,
            provider_key=source.provider_key,
            model=source.model,
            target_default=source.target_default,
        )
    )
    await db.flush()

    stmt = select(Message).where(
        Message.conversation_id == conversation_id, Message.deleted_at.is_(None)
    )
    if up_to_message_id is not None:
        cutoff = (
            await db.execute(
                select(Message.created_at).where(Message.id == up_to_message_id)
            )
        ).scalar_one_or_none()
        if cutoff is not None:
            stmt = stmt.where(Message.created_at <= cutoff)

    count = 0
    last_at = None
    for msg in (await db.execute(stmt.order_by(Message.created_at))).scalars():
        db.add(
            Message(
                id=uuid4(),
                user_id=user.id,
                conversation_id=new_id,
                role=msg.role,
                content=msg.content,
                tool_calls=msg.tool_calls,
                tool_call_id=msg.tool_call_id,
                provider_key=msg.provider_key,
                model=msg.model,
            )
        )
        count += 1
        last_at = msg.created_at

    await db.execute(
        update(Conversation)
        .where(Conversation.id == new_id)
        .values(message_count=count, last_message_at=last_at)
    )
    row = (
        await db.execute(select(Conversation).where(Conversation.id == new_id))
    ).scalar_one()
    return ConversationOut.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------
@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = None,
) -> MessageList:
    await _load_conversation(db, user, conversation_id)

    # Copia o que o worker produziu e aplica o título sugerido (§6.2.2).
    await drain_turn_outputs(db, user.id, conversation_id)
    await apply_title_suggestion(db, user.id, conversation_id)

    # Promove rascunhos de plano aqui também, e não só no `GET /plans`.
    #
    # A promoção era preguiçosa de um jeito que dependia do RELÓGIO DO
    # NAVEGADOR: a tela buscava `/plans` ao receber o evento `plan`, e
    # se essa busca chegasse antes do rascunho ser gravado, nada mais
    # buscava e a proposta ficava invisível para sempre.
    #
    # Medido na conta do usuário: busca às 20:24:12.77, rascunho às
    # 20:24:15 — 2,2 s de diferença, e o `promoted` ficou `false` até
    # eu ir ver. Três rascunhos estavam presos assim.
    #
    # Esta rota é chamada no `done` de todo turno, então promover aqui
    # tira o navegador da equação: quando a resposta aparece na tela, o
    # plano já existe.
    await promote_drafts(db, user.id)

    stmt = select(Message).where(
        Message.conversation_id == conversation_id, Message.deleted_at.is_(None)
    )
    if cursor:
        try:
            stmt = stmt.where(Message.created_at > datetime.fromisoformat(cursor))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": "invalid_cursor", "message": "Cursor inválido."}
                },
            ) from exc

    rows = list(
        (await db.execute(stmt.order_by(Message.created_at).limit(limit + 1))).scalars()
    )
    has_more = len(rows) > limit
    page = rows[:limit]

    return MessageList(
        items=[MessageOut.model_validate(m, from_attributes=True) for m in page],
        next_cursor=page[-1].created_at.isoformat() if has_more and page else None,
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}", status_code=204
)
async def delete_message(
    conversation_id: UUID, message_id: UUID, user: CurrentUser, db: DbSession
) -> None:
    await _load_conversation(db, user, conversation_id)
    await db.execute(
        update(Message)
        .where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
            Message.user_id == user.id,
        )
        .values(deleted_at=datetime.now(UTC))
    )


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def send_message(
    conversation_id: UUID,
    body: SendMessage,
    user: CurrentUser,
    db: DbSession,
) -> TurnAccepted:
    """Persiste a mensagem, enfileira o turno e responde na hora.

    Não espera o modelo: é o ponto central da separação por perfil de
    execução (§2.3). A resposta chega em milissegundos mesmo que o turno
    leve minutos.

    **O limite mais importante do sistema** (§2219: "limite separado e
    mais baixo para rotas que disparam LLM"). Cada chamada aceita aqui
    enfileira um turno que vai gastar token de uma chave de API real e
    ocupar o worker por até 5 minutos. É a única rota em que estourar o
    limite custa dinheiro ao usuário, e a resposta 202 imediata esconde
    isso: o cliente não sente a demora que normalmente freia um laço.

    Limitado por usuário, não por IP: o custo segue a chave, que é da
    conta.
    """
    await enforce(
        "llm:turn",
        str(user.id),
        get_settings().rate_limit_llm_per_user,
        "Muitas mensagens em pouco tempo. Aguarde um momento antes de "
        "enviar a próxima.",
    )

    conversation = await _load_conversation(db, user, conversation_id)

    turn_id = body.client_turn_id or uuid4()

    # Idempotência no envio (§5.1). A guarda vive em `core.messages`,
    # não em `ai.turns`: aquela linha só existe depois que o worker
    # consome o job, e entre dois POSTs de um duplo-clique ainda não
    # existe — os dois passavam. Quem arbitra corrida é o banco, no
    # recurso que ESTA operação cria.
    if body.client_turn_id is not None:
        already = (
            await db.execute(
                select(Message.id).where(
                    Message.client_turn_id == body.client_turn_id,
                    Message.user_id == user.id,
                )
            )
        ).scalar_one_or_none()

        if already is not None:
            return TurnAccepted(
                turn_id=turn_id,
                message_id=already,
                stream_url=f"/api/v1/turns/{turn_id}/stream",
            )

    message_id = uuid4()
    db.add(
        Message(
            id=message_id,
            user_id=user.id,
            conversation_id=conversation_id,
            role="user",
            content=body.content,
            client_turn_id=body.client_turn_id,
        )
    )

    try:
        # Flush antes de enfileirar: se o índice único acusar duplicata,
        # queremos saber ANTES de o worker gastar tokens.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing_id = (
            await db.execute(
                select(Message.id).where(
                    Message.client_turn_id == body.client_turn_id,
                    Message.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        return TurnAccepted(
            turn_id=turn_id,
            message_id=existing_id or message_id,
            stream_url=f"/api/v1/turns/{turn_id}/stream",
        )

    # Na MESMA transacao do INSERT da mensagem: se o envio falhar, os
    # anexos continuam orfaos e reutilizaveis, em vez de apontarem para
    # uma mensagem que nao existe. E DEPOIS do except IntegrityError,
    # para nao amarrar anexo a uma mensagem que o indice unico recusou.
    await vincular_anexos(
        db,
        user_id=user.id,
        attachment_ids=body.attachment_ids,
        message_id=message_id,
        conversation_id=conversation_id,
    )

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            last_message_at=datetime.now(UTC),
            message_count=Conversation.message_count + 1,
            updated_at=datetime.now(UTC),
        )
    )
    # A linha em `ai.turns` é criada pelo WORKER ao consumir, não aqui:
    # `core-api` só tem SELECT no schema `ai` (§2.3.2), e furar essa
    # fronteira para ganhar conveniência é exatamente como ela apodrece.
    # A idempotência do envio é garantida pelo `_job_id` do ARQ, que
    # recusa job duplicado com o mesmo id.

    is_first_exchange = conversation.message_count <= 1
    # Do contexto, não do header: `TraceMiddleware` já sanitizou o valor
    # e gerou um quando a requisição chegou sem nenhum. Ler o header
    # cru aqui faria o job viajar com `None` em toda chamada que não
    # passa pelo BFF — smoke test, `curl`, `/docs` — que são justamente
    # as que alguém roda para depurar.
    trace_id = current_trace_id()

    arq = await get_arq()
    await arq.enqueue_job(
        JobName.RUN_TURN,
        {
            "contract_version": CONTRACT_VERSION,
            "turn_id": str(turn_id),
            "conversation_id": str(conversation_id),
            "user_id": str(user.id),
            "message_id": str(message_id),
            "trace_id": trace_id,
        },
        _job_id=f"turn:{turn_id}",
    )

    # Título depois da primeira troca (§6.2.2). Job separado e de baixa
    # prioridade: falhar não afeta a conversa.
    if is_first_exchange and conversation.title is None:
        await arq.enqueue_job(
            JobName.TITLE_CONVERSATION,
            {
                "contract_version": CONTRACT_VERSION,
                "conversation_id": str(conversation_id),
                "user_id": str(user.id),
                "trace_id": trace_id,
            },
            _job_id=f"title:{conversation_id}",
            _defer_by=3,  # dá tempo de a resposta começar
        )

    return TurnAccepted(
        turn_id=turn_id,
        message_id=message_id,
        stream_url=f"/api/v1/turns/{turn_id}/stream",
    )


# ---------------------------------------------------------------
# Turnos: o stream vive aqui (§7.1)
# ---------------------------------------------------------------
turns = APIRouter(prefix="/turns", tags=["turns"])


@turns.get("/{turn_id}/stream")
async def stream_turn(
    turn_id: UUID,
    user: CurrentUser,
    request: Request,
    last_event_id: str | None = Query(default=None),
) -> EventSourceResponse:
    """Relay do Redis Stream como SSE.

    O browser nunca fala com o worker: o worker publica, este endpoint
    assina e repassa. É o que permite reiniciar o worker sem derrubar
    conexão de usuário (§2.3.1).
    """
    redis = get_redis()
    key = turn_stream_key(str(turn_id))

    async def event_source():
        cursor = last_event_id or "0-0"
        idle_ms = 15_000  # heartbeat: proxies derrubam conexão ociosa

        while True:
            if await request.is_disconnected():
                break

            entries = await redis.xread({key: cursor}, count=50, block=idle_ms)
            if not entries:
                yield {"event": "ping", "data": ""}
                continue

            for _stream, messages in entries:
                for msg_id, fields in messages:
                    cursor = msg_id
                    payload = fields.get("data")
                    if payload is None:
                        continue
                    event = json.loads(payload)
                    yield {
                        "id": msg_id,
                        "event": event.get("type", "message"),
                        "data": payload,
                    }
                    if event.get("type") in {"done", "error"}:
                        return

    return EventSourceResponse(event_source(), ping=None)


@turns.get("/{turn_id}")
async def get_turn(turn_id: UUID, user: CurrentUser, db: DbSession) -> dict:
    """Estado do turno, para reload de página não perder o contexto."""
    row = (
        await db.execute(
            text(
                """
                SELECT status, provider_key, model, prompt_tokens,
                       completion_tokens, error_code, created_at, finished_at
                FROM ai.turns WHERE id = :tid AND user_id = :uid
                """
            ),
            {"tid": str(turn_id), "uid": str(user.id)},
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {"code": "turn_not_found", "message": "Turno não encontrado."}
            },
        )

    event_count = await get_redis().xlen(turn_stream_key(str(turn_id)))
    return {
        "turn_id": str(turn_id),
        "status": row[0],
        "provider_key": row[1],
        "model": row[2],
        "prompt_tokens": row[3],
        "completion_tokens": row[4],
        "error_code": row[5],
        "event_count": event_count,
    }


@turns.post("/{turn_id}/cancel", status_code=202)
async def cancel_turn(turn_id: UUID, user: CurrentUser) -> dict:
    """Cancelamento explícito.

    Fechar a aba NÃO cancela (§7.1): os tokens já foram pagos quando a
    chamada saiu, então jogar o resultado fora seria desperdício.
    """
    settings = get_settings()
    redis = get_redis()
    await redis.setex(
        turn_cancel_key(str(turn_id)), settings.turn_stream_ttl_seconds, "1"
    )

    # Avisa o worker também, para ele não esperar a próxima iteração.
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            await client.post(
                f"{settings.ai_worker_internal_url}/internal/turns/{turn_id}/cancel",
                headers={
                    "Authorization": f"Bearer {create_service_token('ai-worker')}"
                },
            )
        except httpx.HTTPError:
            # A flag no Redis já basta; o worker a checa entre iterações.
            log.info("worker não respondeu ao cancelamento de %s", turn_id)

    return {"turn_id": str(turn_id), "status": "cancel_requested"}
