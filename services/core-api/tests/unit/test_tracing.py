"""Testes do `trace_id` (§12) no core-api.

O que está sendo verificado tem duas metades de natureza bem diferente:

1. **Correlação** — o id entra por header e sai nas linhas de log e na
   resposta. É o que torna "por que o token não chegou?" investigável
   (§14.2, risco número um).
2. **Segurança** — o valor vem de fora, e o log é JSON. Sem sanitização,
   um header forja linhas de log, que é um ataque contra quem LÊ o log.

A segunda metade é a que mais merece teste, porque falha em silêncio: um
trace sujo continua "funcionando" até alguém precisar confiar no log.
"""

from __future__ import annotations

import json
import logging
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("JWT_SECRET", "j" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.core import tracing  # noqa: E402


@pytest.fixture(autouse=True)
def contexto_limpo():
    """Zera os `ContextVar` entre testes.

    Sem isto a ordem dos testes passa a importar: um teste que seta
    trace deixaria o valor visível para o seguinte, e o seguinte
    passaria por motivo errado.
    """
    tracing.set_trace_id(None)
    tracing.set_user_id(None)
    yield
    tracing.set_trace_id(None)
    tracing.set_user_id(None)


# ---------------------------------------------------------------
# Sanitização: a parte de segurança
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    "entrada,esperado",
    [
        # UUID com hífen, que é o que o BFF gera com `crypto.randomUUID`.
        (
            "3f2b1a44-0000-4aaa-8bbb-99cc00112233",
            "3f2b1a44-0000-4aaa-8bbb-99cc00112233",
        ),
        ("DEADBEEF", "DEADBEEF"),
        ("com_sublinhado", "com_sublinhado"),
        # Espaço nas pontas é aparado; acento não sobrevive, e não deve:
        # um trace id não tem conteúdo a preservar.
        ("  abc  ", "abc"),
        ("traço", "trao"),
    ],
)
def test_sanitize_preserva_o_que_e_seguro(entrada, esperado) -> None:
    assert tracing.sanitize(entrada) == esperado


def test_sanitize_remove_o_que_forjaria_linha_de_log() -> None:
    """O ataque concreto: fechar o JSON e abrir um registro novo.

    Um `X-Trace-Id` assim, interpolado cru no formato de log, produziria
    DUAS linhas JSON válidas — a segunda inteiramente escolhida por quem
    mandou o header, com o nível que quisesse. Quem lê log é um
    agregador que confia no formato.
    """
    ataque = '","level":"CRITICAL","msg":"banco de dados apagado'
    limpo = tracing.sanitize(ataque)

    assert limpo is not None
    for proibido in ['"', ",", ":", " "]:
        assert proibido not in limpo


def test_sanitize_recusa_vazio_e_só_lixo() -> None:
    """`None`, e não string vazia: "sem trace" é um estado.

    Com `""`, o log sairia como `"trace_id":""` — que parece trace
    perdido, e manda quem depura procurar um id que nunca existiu.
    """
    assert tracing.sanitize(None) is None
    assert tracing.sanitize("") is None
    assert tracing.sanitize("   ") is None
    assert tracing.sanitize('<>"{}') is None


def test_sanitize_trunca() -> None:
    """Header é entrada não confiável de tamanho arbitrário, e cada
    linha de log carregaria o valor inteiro."""
    assert len(tracing.sanitize("a" * 500) or "") == 64


# ---------------------------------------------------------------
# O filtro: é aqui que o log ganha o campo
# ---------------------------------------------------------------
def test_filtro_injeta_trace_e_user_em_log_ja_escrito(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """O ponto todo do desenho: `log.info` SEM argumento de trace.

    Se este teste exigisse passar o trace na chamada, o desenho teria
    falhado — seria preciso editar todo ponto de log do projeto, e cada
    ponto novo nasceria sem ele.
    """
    logger = logging.getLogger("teste.filtro")
    logger.addFilter(tracing.TraceFilter())

    tracing.set_trace_id("trace-abc")
    tracing.set_user_id("11111111-1111-1111-1111-111111111111")

    with caplog.at_level(logging.INFO, logger="teste.filtro"):
        logger.info("alguma coisa aconteceu")

    registro = caplog.records[-1]
    assert registro.trace_id == "trace-abc"
    assert registro.user_id == "11111111-1111-1111-1111-111111111111"


def test_filtro_usa_traco_quando_nao_ha_contexto(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Startup e shutdown logam fora de request.

    "-" literal em vez de campo ausente: com o campo faltando, o
    `%(trace_id)s` do formato levanta `ValueError` e a linha de log
    desaparece — justamente nas linhas de subida, que são as que contam
    quando o serviço não sobe.
    """
    logger = logging.getLogger("teste.sem-contexto")
    logger.addFilter(tracing.TraceFilter())

    with caplog.at_level(logging.INFO, logger="teste.sem-contexto"):
        logger.info("core-api subindo")

    assert caplog.records[-1].trace_id == "-"
    assert caplog.records[-1].user_id == "-"


def test_a_linha_formatada_e_json_valido() -> None:
    """Prova o formato de verdade, não só os atributos.

    O trace vem de header, e o alvo da injeção é quem LÊ o log. Duas
    defesas independentes respondem por isso: `sanitize` limpa o valor,
    e `json.dumps` escapa o que passar. Este teste prova a segunda —
    ele passa mesmo com um trace sujo, e é essa a intenção.
    """
    logger = logging.getLogger("teste.json")
    tracing.set_trace_id('","level":"CRITICAL","msg":"forjado')

    registro = logger.makeRecord(
        "teste.json", logging.INFO, "f", 1, "mensagem real", None, None
    )
    tracing.TraceFilter().filter(registro)

    linha = tracing.TraceFormatter().format(registro)
    # Uma linha, um objeto: se o trace tivesse escapado o envelope,
    # `loads` falharia com "extra data" — esse é o ataque.
    dados = json.loads(linha)
    assert dados["msg"] == "mensagem real"
    assert dados["level"] == "INFO"


def test_mensagem_com_aspas_sai_em_json_valido() -> None:
    """O defeito medido no serviço rodando, não imaginado.

    A linha de acesso do uvicorn contém aspas:

        172.19.0.1 - "GET /healthz HTTP/1.1" 200

    Com o formato interpolado, ela saía como JSON inválido — a linha
    mais volumosa do core-api, descartável por qualquer agregador, e em
    silêncio, porque log malformado não dá erro em quem o emite.
    """
    registro = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access",
        logging.INFO,
        "f",
        1,
        '%s - "%s %s" %d',
        ("172.19.0.1", "GET", "/healthz HTTP/1.1", 200),
        None,
    )
    dados = json.loads(tracing.TraceFormatter().format(registro))
    assert dados["msg"] == '172.19.0.1 - "GET /healthz HTTP/1.1" 200'


def test_excecao_vai_num_campo_e_nao_em_linhas_soltas() -> None:
    """Um traceback multilinha quebra "uma linha, um evento".

    Sem isto, uma exceção produz uma linha JSON seguida de dez linhas
    de texto cru — e o agregador trata cada uma como evento separado,
    nenhum deles com trace.
    """
    try:
        raise ValueError("falhou")
    except ValueError:
        import sys

        registro = logging.getLogger("x").makeRecord(
            "x", logging.ERROR, "f", 1, "deu erro", None, sys.exc_info()
        )

    linha = tracing.TraceFormatter().format(registro)
    assert linha.count("\n") == 0
    dados = json.loads(linha)
    assert "ValueError: falhou" in dados["exc"]


# ---------------------------------------------------------------
# `configure_logging`: o defeito que só a execução real revelou
# ---------------------------------------------------------------
def test_log_de_biblioteca_nao_morre_por_falta_do_campo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """O bug que passou pelos testes e apareceu no primeiro `docker logs`.

    `uvicorn.access`, `arq.worker` e `sqlalchemy.engine` logam em
    loggers FILHOS. Um `Filter` posto no logger raiz não roda para
    registros que chegam por propagação — eles vão direto ao handler.
    Com `%(trace_id)s` no formato e o filtro no logger, cada uma dessas
    linhas morria com `ValueError: Formatting field not found`.

    O modo de falha é o que torna isto grave: a linha não sai sem
    trace, ela **não sai**. A instrumentação apagava justamente o log
    que se usaria para investigar.

    Este teste reproduz a propagação de verdade — logger filho, nada
    configurado nele — e é o que a suíte não tinha.
    """
    tracing.configure_logging(logging.INFO)
    tracing.set_trace_id("trace-de-biblioteca")

    logging.getLogger("uvicorn.access").info("GET /api/v1/x 200")

    saida = capsys.readouterr().err
    assert "Formatting field not found" not in saida
    assert "--- Logging error ---" not in saida

    linha = json.loads(saida.strip().splitlines()[-1])
    assert linha["trace_id"] == "trace-de-biblioteca"
    assert linha["logger"] == "uvicorn.access"


def test_configure_logging_nao_duplica_linha(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Chamada duas vezes (reload do uvicorn em dev, import repetido)
    não pode acumular handler: dois handlers escrevem a mesma linha
    duas vezes, e um log duplicado faz contar errado qualquer coisa
    que se tente medir nele."""
    tracing.configure_logging(logging.INFO)
    tracing.configure_logging(logging.INFO)

    logging.getLogger("teste.dup").info("uma vez só")
    linhas = [
        linha
        for linha in capsys.readouterr().err.strip().splitlines()
        if "uma vez" in linha
    ]
    assert len(linhas) == 1


def test_formatter_sozinho_ja_garante_os_campos() -> None:
    """Cinto e suspensório: mesmo sem o filtro, o formatter preenche.

    É o que impede que um handler adicionado depois de
    `configure_logging` — um `FileHandler` em produção, por exemplo —
    reintroduza o mesmo apagão de log.
    """
    registro = logging.getLogger("x").makeRecord(
        "x", logging.INFO, "f", 1, "sem filtro nenhum", None, None
    )
    assert not hasattr(registro, "trace_id")

    linha = tracing.TraceFormatter().format(registro)
    assert json.loads(linha)["msg"] == "sem filtro nenhum"


# ---------------------------------------------------------------
# O middleware
# ---------------------------------------------------------------
def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "headers": headers or [], "path": "/x"}


async def _coletar(scope: dict) -> dict:
    """Roda o middleware em volta de uma app de mentira e devolve o que
    ela viu no contexto e o que saiu na resposta."""
    visto: dict = {}

    async def app(scope, receive, send):
        visto["trace_no_contexto"] = tracing.current_trace_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message):
        if message["type"] == "http.response.start":
            visto["headers"] = message["headers"]

    await tracing.TraceMiddleware(app)(scope, None, send)  # type: ignore[arg-type]
    return visto


async def test_middleware_adota_o_trace_do_bff() -> None:
    """O caso normal: o id nasce no BFF e o core-api o adota.

    Gerar um novo aqui quebraria o trace no primeiro salto, que é o
    oposto do objetivo.
    """
    visto = await _coletar(_scope([(b"x-trace-id", b"do-navegador")]))

    assert visto["trace_no_contexto"] == "do-navegador"


async def test_middleware_gera_quando_nao_vem_nenhum() -> None:
    """Chamada direta ao core-api (smoke, `curl`, `/docs`) também merece
    trace: sem id, as linhas daquela requisição não se agrupam nem entre
    si."""
    visto = await _coletar(_scope())

    assert visto["trace_no_contexto"]
    assert len(visto["trace_no_contexto"]) == 32


async def test_middleware_sanitiza_o_header() -> None:
    visto = await _coletar(
        _scope([(b"x-trace-id", b'aa","level":"CRITICAL')])
    )

    assert '"' not in visto["trace_no_contexto"]


async def test_middleware_devolve_o_trace_na_resposta() -> None:
    """Para quem reporta um erro poder dizer QUAL requisição foi.

    É o que transforma "deu erro no chat" em algo localizável no log.
    """
    visto = await _coletar(_scope([(b"x-trace-id", b"eco-de-volta")]))

    devolvidos = {k.decode(): v.decode() for k, v in visto["headers"]}
    assert devolvidos["x-trace-id"] == "eco-de-volta"


async def test_middleware_limpa_user_id_entre_requisicoes() -> None:
    """Vazamento de `user_id` entre requisições é atribuição errada.

    O worker ASGI reaproveita contexto; sem o reset, a linha de log de
    quem chegou agora levaria o id de quem acabou de ser atendido — um
    log que mente é pior que um log sem o campo.
    """
    tracing.set_user_id("usuario-anterior")

    registro = logging.getLogger("x").makeRecord(
        "x", logging.INFO, "f", 1, "m", None, None
    )

    async def app(scope, receive, send):
        # Dentro do request seguinte, ANTES de qualquer autenticação:
        # é o instante em que o id antigo ainda estaria visível.
        tracing.TraceFilter().filter(registro)
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(_message):
        pass

    await tracing.TraceMiddleware(app)(_scope(), None, send)  # type: ignore[arg-type]
    assert registro.user_id == "-"


async def test_middleware_ignora_o_que_nao_e_http() -> None:
    """`lifespan` e websocket passam pelo mesmo middleware, e tratá-los
    como request faria o startup quebrar."""
    chamou = {"n": 0}

    async def app(scope, receive, send):
        chamou["n"] += 1

    await tracing.TraceMiddleware(app)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert chamou["n"] == 1
