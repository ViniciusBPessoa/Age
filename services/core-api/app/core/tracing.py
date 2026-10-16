"""O `trace_id` que atravessa os três serviços (§12).

ARQUITETURA.md §14.2 lista como risco número um a complexidade
operacional de três serviços para um dev solo, e nomeia a mitigação:

  "Tracing com `trace_id` propagado do BFF até o worker (§12) — sem
  isso, depurar 'o token não chegou' é adivinhação."

O trace nasce no BFF (`forwardHeaders`, em `app/api/[...path]/route.ts`),
chega aqui como header `X-Trace-Id`, e segue no payload da fila até o
worker. Essa parte da tubulação já existia. O que faltava era **alguém
bebendo dela**: o id chegava e nenhuma linha de log o mencionava, o que
torna a propagação inútil na única hora em que ela importa.

## Por que `contextvars`, e não passar o id de função em função

Passar adiante exigiria mudar a assinatura de toda função que loga, de
`get_current_user` ao `PlanExecutor`. Pior: cada nova função nasceria
sem o parâmetro, e o esquecimento é silencioso — a linha de log sai sem
trace e ninguém nota até precisar dela.

Com `ContextVar` + um filtro de logging, **todo** `log.info` já escrito
nos dois serviços ganha o campo sem tocar no ponto de chamada. Um
`ContextVar` é propagado automaticamente para as tasks criadas dentro do
contexto, que é exatamente o escopo de um request no FastAPI.

## Por que o id tem de ser sanitizado

O valor vem de header, ou seja, de fora. Jogá-lo cru numa linha de log
JSON permite que alguém mande `X-Trace-Id: ","level":"CRITICAL","msg":"`
e forje linhas de log — injeção de log, que não é teórica: quem lê log é
um agregador que confia no formato. O filtro trunca e remove tudo que não
seja seguro.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "x-trace-id"

# Nome do campo no log: §12 pede `request_id`. Mantemos os dois nomes
# apontando para o mesmo valor — `trace_id` é como o contrato da fila e o
# BFF o chamam, e renomear um dos lados criaria dois conceitos onde há um.
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)

# Hex, hífen e sublinhado cobrem UUID (o que o BFF gera) e os ids de
# trace do OpenTelemetry, para o dia em que §12 for cumprido por
# inteiro. Qualquer outro caractere é descartado em vez de escapado:
# um trace id não tem conteúdo a preservar, só serve para casar com
# outras linhas, e descartar é mais simples de auditar que escapar.
_SEGURO = re.compile(r"[^A-Za-z0-9_-]")
_MAX = 64


def sanitize(raw: str | None) -> str | None:
    """Reduz um valor de header a algo seguro para log — ou a `None`.

    Devolve `None` para entrada vazia ou que não sobrou nada depois da
    limpeza, e não uma string vazia: "não tem trace" é um estado, e
    representá-lo como `""` faria o filtro emitir `"trace_id": ""`, que
    parece trace perdido em vez de trace ausente.
    """
    if not raw:
        return None
    limpo = _SEGURO.sub("", raw.strip())[:_MAX]
    return limpo or None


def new_trace_id() -> str:
    """Um trace novo, para quando a requisição chega sem nenhum.

    Acontece em dois casos legítimos: chamada direta ao core-api (smoke
    test, `curl`, o próprio `/docs`) e job de cron, que não tem request
    de origem. Gerar aqui é melhor que deixar vazio — sem id, as linhas
    daquele request não se agrupam nem entre si.
    """
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(value: str | None) -> None:
    _trace_id.set(sanitize(value))


def set_user_id(value: str | None) -> None:
    """§12 pede `user_id` no log. Id, nunca e-mail: §9.1 trata e-mail
    como dado pessoal, e log é o lugar de onde é mais difícil apagar."""
    _user_id.set(sanitize(value))


class TraceFormatter(logging.Formatter):
    """Monta a linha de log JSON — com `json.dumps`, não com `%`.

    ## Por que não interpolar o formato

    O formato antigo era uma string com `%(message)s` dentro de chaves,
    o que **parece** JSON e não é: nada escapa o conteúdo. Medido no
    serviço rodando, a linha de acesso do uvicorn —

        172.19.0.1 - "GET /healthz HTTP/1.1" 200

    — tem aspas, e a linha saía como JSON inválido. Era a linha mais
    volumosa do core-api, e um agregador a descartaria inteira. Pior:
    descartaria em silêncio, porque log malformado não dá erro em
    quem o emite.

    `json.dumps` escapa aspas, barras e controle por construção, e é o
    que torna a sanitização de `trace_id` uma defesa em profundidade em
    vez da única.

    ## Por que ainda preencher os campos aqui

    O modo de falha de um campo ausente não é "linha sem trace", é
    **linha nenhuma**: `ValueError` dentro do logging, e a mensagem
    vira um despejo de stack trace. Um log que se apaga quando a
    instrumentação tem defeito remove justamente a ferramenta que se
    usaria para investigar.
    """

    def format(self, record: logging.LogRecord) -> str:
        linha = {
            "level": record.levelname,
            "logger": record.name,
            # Do registro quando o filtro já passou; do contexto quando
            # não (handler adicionado depois de `configure_logging`).
            "trace_id": getattr(record, "trace_id", None)
            or _trace_id.get()
            or "-",
            "user_id": getattr(record, "user_id", None) or _user_id.get() or "-",
            "msg": record.getMessage(),
        }
        if record.exc_info:
            # Exceção vai num campo, não solta em linhas seguintes: um
            # traceback multilinha quebra a premissa de "uma linha, um
            # evento" em que todo agregador se apoia.
            linha["exc"] = self.formatException(record.exc_info)
        # `ensure_ascii=False` preserva as mensagens em português; o
        # escape de aspas e controle acontece de todo modo.
        return json.dumps(linha, ensure_ascii=False)


class TraceFilter(logging.Filter):
    """Injeta `trace_id` e `user_id` em todo registro de log.

    É um `Filter` e não um `Formatter` porque o formato já está definido
    nos dois serviços (JSON, em `main.py` e `worker.py`); um filtro
    acrescenta os campos sem disputar quem formata.

    Nunca levanta: um erro aqui apagaria a linha de log que se está
    tentando enriquecer, e perder o log é pior que perder o trace.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get() or "-"
        record.user_id = _user_id.get() or "-"
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Log estruturado JSON com `trace_id` e `user_id` (§12).

    Substitui o `logging.basicConfig` + `install` que estavam
    espalhados: o formato e o filtro têm de ser instalados juntos, e
    separá-los foi exatamente o que permitiu o formato entrar sem o
    filtro chegar nos handlers.

    §12 proíbe conteúdo de mensagem no log, e isto não o impede — é
    responsabilidade de quem chama `log.info`. O que esta função
    garante é o envelope.

    ## Por que o filtro vai no HANDLER, e não no logger

    A distinção não é cosmética, e custou uma depuração. Um
    `logging.Filter` adicionado a um *logger* só roda para registros
    emitidos **naquele** logger; os que sobem por propagação dos filhos
    (`uvicorn.access`, `arq.worker`, `sqlalchemy.engine`) chegam ao
    handler do raiz sem nunca passar pelos filtros do raiz. É assim que
    a API de logging funciona, e é fácil de ler errado.

    O sintoma foi espetacular: com `%(trace_id)s` no formato e o filtro
    no logger, toda linha vinda de biblioteca morria com `ValueError:
    Formatting field not found in record`. O log não ficava sem trace —
    ficava **sem linha**, substituída por um despejo de stack trace.

    Handler é o ponto certo porque é por onde todo registro passa,
    próprio ou propagado, imediatamente antes de ser formatado.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(TraceFormatter())
    handler.addFilter(TraceFilter())

    raiz = logging.getLogger()
    # `force`-equivalente: `basicConfig` não substitui handler existente,
    # e dois handlers duplicariam cada linha.
    for antigo in list(raiz.handlers):
        raiz.removeHandler(antigo)
    raiz.addHandler(handler)
    raiz.setLevel(level)

    # O uvicorn aplica o LOGGING_CONFIG dele ao SUBIR, depois deste
    # módulo ser importado, e nele `uvicorn.access` ganha handler
    # próprio com `propagate: False`. Resultado medido: as linhas de
    # acesso saíam no formato de texto do uvicorn, sem trace, enquanto
    # as nossas saíam em JSON — o log mais volumoso do serviço, que é
    # justamente o que diz qual requisição chegou, ficava de fora.
    #
    # Tirar os handlers e religar a propagação devolve essas linhas ao
    # nosso handler. Não é monkeypatch: é a configuração pública desses
    # loggers, e o uvicorn documenta que eles existem para isso.
    for nome in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(nome)
        for antigo in list(lg.handlers):
            lg.removeHandler(antigo)
        lg.propagate = True


class TraceMiddleware:
    """ASGI puro, e não `BaseHTTPMiddleware`, por um motivo concreto.

    `BaseHTTPMiddleware` roda o endpoint numa task separada via
    `anyio`, e o valor setado num `ContextVar` dentro do middleware não
    é visto de forma confiável do outro lado dessa fronteira. Já custou
    tempo a muita gente. Middleware ASGI chama a aplicação no mesmo
    contexto, então o `set` vale para o request inteiro.

    Vem antes de qualquer autenticação: o trace precisa existir também
    nas requisições que falham em 401, que são exatamente as que alguém
    vai querer investigar.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        bruto = next(
            (
                v.decode("latin-1")
                for k, v in scope.get("headers", [])
                if k.decode("latin-1").lower() == HEADER
            ),
            None,
        )
        trace = sanitize(bruto) or new_trace_id()
        _trace_id.set(trace)
        # Zerado por request: o worker ASGI reaproveita contexto entre
        # requisições, e sem isto o `user_id` de quem acabou de ser
        # atendido vazaria para a próxima linha de log.
        _user_id.set(None)

        async def send_com_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Devolvido ao cliente para quem reporta um erro poder
                # dizer qual requisição foi — é o que transforma "deu
                # erro" em algo investigável.
                headers = list(message.get("headers", []))
                headers.append((HEADER.encode(), trace.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_com_trace)
