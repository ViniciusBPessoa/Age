"""Testes do `trace_id` no worker — o salto que estava quebrado (§12).

ARQUITETURA.md §12 é específico sobre onde dói:

  "Sem a propagação pela fila, o trace quebra exatamente no ponto mais
  difícil de depurar."

A propagação pela fila existia: o `core-api` punha `trace_id` no payload
e `age_contracts.jobs._BasePayload` o declarava desde o M0. O que não
existia era o consumo — `run_turn` lia três campos do payload e
descartava o quarto. Estes testes travam o consumo.
"""

from __future__ import annotations

import json
import logging
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app import tracing  # noqa: E402


@pytest.fixture(autouse=True)
def contexto_limpo():
    tracing.bind({}, user_id=None)
    yield
    tracing.bind({}, user_id=None)


# ---------------------------------------------------------------
# `bind`: a entrada de todo job
# ---------------------------------------------------------------
def test_bind_adota_o_trace_do_payload() -> None:
    """O caso que fecha o círculo: o id que o navegador gerou chega ao
    worker e passa a valer para as linhas de log de lá."""
    trace = tracing.bind(
        {"trace_id": "nasceu-no-bff", "turn_id": "t1"}, user_id="u1"
    )

    assert trace == "nasceu-no-bff"
    assert tracing.current_trace_id() == "nasceu-no-bff"


def test_bind_gera_quando_o_payload_nao_traz() -> None:
    """Job de cron não tem requisição de origem.

    Sem gerar aqui, as linhas de uma execução de cron não se agrupariam
    nem entre si — e um expurgo que apagou o que não devia seria
    impossível de reconstituir.
    """
    trace = tracing.bind({"conversation_id": "c1"})

    assert trace
    assert tracing.current_trace_id() == trace


def test_bind_sanitiza_o_que_veio_da_fila() -> None:
    """O valor atravessou a fila sem ninguém validar.

    Ele nasceu de um header HTTP no BFF, e a fila não o tornou
    confiável — só o transportou. Tratá-lo como interno aqui seria
    confiar num dado externo por ter mudado de meio.
    """
    tracing.bind({"trace_id": '","level":"CRITICAL","msg":"forjado'})

    atual = tracing.current_trace_id() or ""
    assert '"' not in atual
    assert ":" not in atual


def test_bind_limpa_o_user_do_job_anterior() -> None:
    """O `arq` reusa a task entre jobs em sequência.

    Sem o reset, as linhas do turno de Bruno sairiam com o id de Ana —
    e um log que atribui a pessoa errada é pior que um log sem o campo,
    porque dá uma resposta falsa a "quem foi afetado?".
    """
    tracing.bind({"trace_id": "a"}, user_id="ana")
    tracing.bind({"trace_id": "b"})  # job seguinte, sem user

    registro = logging.getLogger("x").makeRecord(
        "x", logging.INFO, "f", 1, "m", None, None
    )
    tracing.TraceFilter().filter(registro)
    assert registro.user_id == "-"


def test_bind_tolera_trace_nulo_no_payload() -> None:
    """`trace_id: None` é o que o contrato permite (campo opcional), e é
    o que chega de job enfileirado antes desta mudança — reentrega de
    job antigo não pode derrubar o worker."""
    assert tracing.bind({"trace_id": None, "turn_id": "t"}) is not None


# ---------------------------------------------------------------
# O filtro, igual ao do core-api de propósito
# ---------------------------------------------------------------
def test_formato_do_worker_bate_com_o_do_core_api() -> None:
    """O objetivo prático é `grep` de um id nos logs dos dois serviços
    e ler o resultado como UMA sequência. Formatos diferentes
    inviabilizariam isso mesmo com o id propagado certo."""
    tracing.bind({"trace_id": "mesmo-id"}, user_id="u1")
    registro = logging.getLogger("ai-worker.x").makeRecord(
        "ai-worker.x", logging.INFO, "f", 1, "turno iniciado", None, None
    )
    tracing.TraceFilter().filter(registro)

    dados = json.loads(tracing.TraceFormatter().format(registro))
    assert dados["trace_id"] == "mesmo-id"
    assert dados["user_id"] == "u1"
    assert set(dados) == {"level", "logger", "trace_id", "user_id", "msg"}


def test_log_ja_escrito_ganha_o_trace_sem_mudar_a_chamada(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """As quatro linhas de log que `run_turn.py` já tinha passam a ser
    rastreáveis sem nenhuma edição — era o critério de desenho."""
    logger = logging.getLogger("ai-worker.run_turn.teste")
    logger.addFilter(tracing.TraceFilter())
    tracing.bind({"trace_id": "t-42"}, user_id="u")

    with caplog.at_level(logging.WARNING, logger="ai-worker.run_turn.teste"):
        logger.warning("turno %s falhou no provedor: %s", "abc", "timeout")

    assert caplog.records[-1].trace_id == "t-42"


# ---------------------------------------------------------------
# `configure_logging`: o log do `arq` morria
# ---------------------------------------------------------------
def test_log_do_arq_nao_morre_por_falta_do_campo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """O defeito real, visto no primeiro `docker logs` depois da mudança.

    O `arq` loga início e fim de cada job no logger `arq.worker`, que é
    FILHO do raiz. Um `Filter` no logger raiz não roda para registros
    que chegam por propagação — vão direto ao handler. Com
    `%(trace_id)s` no formato e o filtro no lugar errado, cada linha do
    `arq` morria com `ValueError: Formatting field not found in record`.

    Grave pelo modo de falha: a linha não sai sem trace, **não sai**. O
    worker perdeu o log de "job X terminou em Y segundos", que é o
    primeiro lugar onde se olha quando um turno não responde.
    """
    tracing.configure_logging(logging.INFO)
    tracing.bind({"trace_id": "t-do-arq"}, user_id="u")

    logging.getLogger("arq.worker").info("%6.2fs → %s", 0.02, "run_turn")

    saida = capsys.readouterr().err
    assert "Formatting field not found" not in saida
    assert "--- Logging error ---" not in saida
    assert json.loads(saida.strip().splitlines()[-1])["trace_id"] == "t-do-arq"


def test_mensagem_do_arq_com_aspas_sai_em_json_valido() -> None:
    """O `arq` loga o resultado do job, que contém aspas e chaves:

        {'status': 'done', 'title': "[mock] recebi: ..."}

    Com o formato interpolado em vez de serializado, essa linha saía
    como JSON inválido — e é a linha que diz se o job terminou.
    """
    registro = logging.getLogger("arq.worker").makeRecord(
        "arq.worker",
        logging.INFO,
        "f",
        1,
        "%6.2fs <- %s * %s",
        (0.018, "title:abc:title_conversation", "{'status': 'done'}"),
        None,
    )
    dados = json.loads(tracing.TraceFormatter().format(registro))
    assert "'status': 'done'" in dados["msg"]


# ---------------------------------------------------------------
# O salto worker → core-api
# ---------------------------------------------------------------
def test_calendar_client_manda_o_trace_no_header() -> None:
    """O último salto, e o que faltava fechar.

    O worker lê a agenda por HTTP interno (§3.4). Sem o header, o
    `core-api` gera um trace NOVO para essa chamada e as duas metades da
    mesma operação — o turno e a leitura de agenda que ele provocou —
    ficam sem ligação nenhuma no log.
    """
    from app.calendar_client import CalendarClient

    tracing.bind({"trace_id": "turno-em-curso"}, user_id="u")
    cliente = CalendarClient(user_id="11111111-1111-1111-1111-111111111111")  # type: ignore[arg-type]

    headers = cliente._headers()
    assert headers["X-Trace-Id"] == "turno-em-curso"
    # O JWT de serviço continua lá: o trace é adicional, não substitui a
    # credencial da chamada interna (§2.3.1).
    assert headers["Authorization"].startswith("Bearer ")


def test_calendar_client_omite_o_header_sem_trace() -> None:
    """Melhor ausente que vazio: com `X-Trace-Id: ""`, o middleware do
    core-api sanitiza para `None` e gera um novo — mesmo resultado, com
    um header a mais viajando e uma mentira no `curl -v`."""
    from app.calendar_client import CalendarClient

    tracing.bind({}, user_id=None)
    tracing._trace_id.set(None)

    cliente = CalendarClient(user_id="11111111-1111-1111-1111-111111111111")  # type: ignore[arg-type]
    assert "X-Trace-Id" not in cliente._headers()
