"""Payloads de job que o core-api enfileira e o ai-worker consome.

ARQUITETURA.md §2.3.1: toda tarefa de IA e de sync entra por fila, nunca
por HTTP síncrono.

Atenção ao `user_id`: ele é DADO, não credencial (§4.1.1). Quem garante
que é legítimo é o core-api, que só enfileira depois de validar o JWT.
O worker confia na fila porque a fila não é acessível de fora.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from age_contracts.version import CONTRACT_VERSION


class JobName(StrEnum):
    """Nomes de job registrados no worker.

    O valor é a string usada pelo ARQ, então mudar quebra jobs já
    enfileirados — por isso são explícitos, não derivados do nome da função.
    """

    RUN_TURN = "run_turn"
    SYNC_CALENDAR = "sync_calendar"
    TITLE_CONVERSATION = "title_conversation"
    EXPIRE_PLANS = "expire_plans"
    PURGE_CONVERSATIONS = "purge_conversations"
    # Só do M0: prova a plumbing. Removido quando RUN_TURN existir de fato.
    ECHO = "echo"


class _BasePayload(BaseModel):
    """Campos comuns a todo job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: int = Field(default=CONTRACT_VERSION)
    # Propagado do BFF até o worker para o trace não quebrar na fila (§12).
    trace_id: str | None = None


class TurnPayload(_BasePayload):
    """Um turno de conversa a ser processado pelo orquestrador (§5.1)."""

    turn_id: UUID
    conversation_id: UUID
    user_id: UUID
    message_id: UUID


class SyncCalendarPayload(_BasePayload):
    """Sincronização de um calendário externo (§6.3)."""

    user_id: UUID
    connection_id: UUID
    selection_id: UUID
    full_resync: bool = False


class TitleConversationPayload(_BasePayload):
    """Geração de título, uma vez, após a primeira resposta (§6.2.2)."""

    conversation_id: UUID
    user_id: UUID
