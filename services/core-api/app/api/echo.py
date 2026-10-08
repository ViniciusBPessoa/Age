"""Eco de plumbing — prova que os três serviços conversam.

Este módulo NÃO é produto. Existe para o M0 e será substituído pelos
endpoints reais de conversa no M1. Ele imita de propósito a estrutura
definitiva (ARQUITETURA.md §7.1):

    POST /api/v1/echo            -> 202 {turn_id}, enfileira
    GET  /api/v1/echo/{id}/stream -> SSE, relay do Redis Stream

Assim, o caminho que o produto vai usar está provado antes de qualquer
regra de negócio depender dele.
"""

import json
from uuid import UUID, uuid4

from age_contracts import JobName
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.core.redis import get_arq, get_redis, turn_stream_key

router = APIRouter(prefix="/echo", tags=["ops"])

# Usuário fixo: no M1 isto vem do JWT (§4.1.1).
_DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class EchoRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class EchoAccepted(BaseModel):
    turn_id: UUID
    stream_url: str


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_echo(body: EchoRequest) -> EchoAccepted:
    """Enfileira e devolve na hora.

    Não espera o worker: é o ponto central da separação por perfil de
    execução (§2.3). A resposta chega em milissegundos mesmo que o job
    leve minutos.
    """
    turn_id = uuid4()
    arq = await get_arq()

    await arq.enqueue_job(
        JobName.ECHO,
        {
            "turn_id": str(turn_id),
            "user_id": str(_DEV_USER_ID),
            "message": body.message,
        },
        _job_id=f"echo:{turn_id}",  # idempotência: retry não duplica (§5.1)
    )

    return EchoAccepted(
        turn_id=turn_id,
        stream_url=f"/api/v1/echo/{turn_id}/stream",
    )


@router.get("/{turn_id}/stream")
async def stream_echo(
    turn_id: UUID,
    request: Request,
    last_event_id: str | None = Query(default=None, alias="last_event_id"),
) -> EventSourceResponse:
    """Relay do Redis Stream como SSE.

    O browser nunca fala com o worker: o worker publica, este endpoint
    assina e repassa. É o que permite reiniciar o worker sem derrubar
    conexão de usuário (§2.3.1).
    """
    redis = get_redis()
    key = turn_stream_key(str(turn_id))

    async def event_source():
        # Retomada: relê do último id visto, ou do começo do stream.
        cursor = last_event_id or "0-0"
        idle_ms = 15_000  # heartbeat: proxies derrubam conexão ociosa

        while True:
            if await request.is_disconnected():
                break

            entries = await redis.xread({key: cursor}, count=50, block=idle_ms)

            if not entries:
                # Comentário SSE mantém a conexão viva durante as
                # iterações silenciosas do laço (§5.1).
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

    # ping_message_factory=None: o heartbeat é nosso, acima.
    return EventSourceResponse(event_source(), ping=None)


@router.get("/{turn_id}")
async def get_echo_state(turn_id: UUID) -> dict[str, object]:
    """Estado do turno, para reload de página não perder o contexto."""
    redis = get_redis()
    key = turn_stream_key(str(turn_id))

    exists = await redis.exists(key)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "turn_not_found",
                    "message": "Turno não encontrado ou expirado.",
                }
            },
        )

    length = await redis.xlen(key)
    return {"turn_id": str(turn_id), "event_count": length}
