"""O contrato entre serviços não divergiu.

ARQUITETURA.md §10 (linha "Contrato entre serviços"). Os dois serviços
instalam o mesmo `age-contracts`, mas nada impede alguém de mudar um
payload e esquecer o outro lado. Este teste falha o build nesse caso.
"""

from __future__ import annotations

import json
from uuid import uuid4

from age_contracts import (
    CONTRACT_VERSION,
    DoneEvent,
    ErrorEvent,
    JobName,
    TokenEvent,
    TurnPayload,
    TurnStatus,
    parse_stream_event,
)


def test_contract_version_is_set() -> None:
    assert isinstance(CONTRACT_VERSION, int)
    assert CONTRACT_VERSION >= 1


def test_turn_payload_roundtrip() -> None:
    """O payload sobrevive à serialização da fila.

    A fila transporta JSON, não objetos Python. Se um campo não
    serializar, o job falha no consumo — longe de onde foi produzido.
    """
    payload = TurnPayload(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        message_id=uuid4(),
        trace_id="trace-123",
    )
    raw = json.loads(payload.model_dump_json())
    restored = TurnPayload.model_validate(raw)
    assert restored == payload


def test_turn_payload_rejects_unknown_fields() -> None:
    """`extra="forbid"` é proposital.

    Campo desconhecido significa que os dois lados estão em versões
    diferentes. Falhar é melhor que ignorar em silêncio e processar com
    um formato que não se entende.
    """
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TurnPayload.model_validate(
            {
                "turn_id": str(uuid4()),
                "conversation_id": str(uuid4()),
                "user_id": str(uuid4()),
                "message_id": str(uuid4()),
                "campo_que_nao_existe": 1,
            }
        )


def test_stream_events_are_discriminated() -> None:
    """O relay do core-api precisa reconstruir o evento pelo campo `type`."""
    for event in (
        TokenEvent(delta="olá"),
        DoneEvent(message_id="m1", status=TurnStatus.DONE),
        ErrorEvent(code="provider_timeout", message="demorou", retryable=True),
    ):
        raw = json.loads(event.model_dump_json())
        assert parse_stream_event(raw) == event


def test_job_names_are_stable_strings() -> None:
    """Renomear um valor quebra jobs já enfileirados.

    Este teste é um lembrete deliberado: se ele falhar depois de um
    rename, a correção não é ajustar o teste — é manter o valor antigo.
    """
    assert JobName.RUN_TURN.value == "run_turn"
    assert JobName.SYNC_CALENDAR.value == "sync_calendar"
    assert JobName.TITLE_CONVERSATION.value == "title_conversation"
    assert JobName.ECHO.value == "echo"
