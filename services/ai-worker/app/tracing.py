"""O lado do worker do `trace_id` (§12).

Gêmeo de `core-api/app/core/tracing.py`, e a duplicação é deliberada:
§2.5 reserva `age-contracts` para o que os dois serviços precisam
concordar — nomes de job, formato de evento, versão. O trace aqui não é
contrato, é a mesma receita de logging aplicada duas vezes; pôr um
helper de logging no pacote de contratos faria dele um utilitário
comum, que é como `age-contracts` deixaria de significar algo.

O que **é** contrato é o campo `trace_id` em `_BasePayload`
(`age_contracts/jobs.py`), e esse já existia.

## O ponto que este arquivo conserta

ARQUITETURA.md §12:

  "Sem a propagação pela fila, o trace quebra exatamente no ponto mais
  difícil de depurar."

A propagação pela fila já estava feita — o `core-api` punha `trace_id`
no payload e o contrato o declarava. Mas o worker lia `turn_id`,
`user_id` e `conversation_id` do payload e **descartava o
`trace_id`**: ele chegava e não ia para lugar nenhum. Um trace que
atravessa a fila e morre na entrada do consumidor dá o mesmo trabalho de
um que nunca atravessou.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar

HEADER = "X-Trace-Id"

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)

# Mesma regra do core-api: o valor nasceu de um header HTTP no BFF e
# viajou pela fila sem ninguém o validar, então continua sendo entrada
# externa. Sem isto, um `X-Trace-Id` com aspas e vírgulas forja linhas
# no log JSON do worker.
_SEGURO = re.compile(r"[^A-Za-z0-9_-]")
_MAX = 64


def sanitize(raw: str | None) -> str | None:
    if not raw:
        return None
    limpo = _SEGURO.sub("", str(raw).strip())[:_MAX]
    return limpo or None


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    return _trace_id.get()


def bind(payload: dict, *, user_id: str | None = None) -> str:
    """Põe o trace do payload em contexto, no começo de um job.

    Gera um novo quando o payload não traz nenhum, o que é o caso dos
    jobs de cron (`purge_conversations`): eles não têm request de
    origem, e sem id as linhas daquela execução não se agrupam nem entre
    si.

    Devolve o trace em uso para quem quiser repassá-lo adiante — é o
    valor que `CalendarClient` manda no header da chamada interna.
    """
    trace = sanitize(payload.get("trace_id")) or new_trace_id()
    _trace_id.set(trace)
    # Explícito a cada job, inclusive para limpar: o worker reusa a
    # mesma task do `arq` para jobs em sequência, e sem zerar, o
    # `user_id` do turno anterior apareceria nas linhas do seguinte.
    _user_id.set(sanitize(user_id) if user_id else None)
    return trace


class TraceFilter(logging.Filter):
    """Injeta os campos em todo registro. Nunca levanta — perder a linha
    de log seria pior que perder o trace dela."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get() or "-"
        record.user_id = _user_id.get() or "-"
        return True


class TraceFormatter(logging.Formatter):
    """Monta a linha JSON com `json.dumps`, não com interpolação.

    Mesmo desenho e mesmas razões do core-api
    (`core-api/app/core/tracing.py`): um formato com `%(message)s`
    dentro de chaves parece JSON e não escapa nada, e a mensagem do
    `arq` — que inclui o resultado do job entre aspas — saía como JSON
    inválido, descartável por qualquer agregador e em silêncio.

    E preenche os campos ausentes porque a falha de um `%(trace_id)s`
    sem o atributo não é linha sem trace, é linha nenhuma.
    """

    def format(self, record: logging.LogRecord) -> str:
        linha = {
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", None)
            or _trace_id.get()
            or "-",
            "user_id": getattr(record, "user_id", None) or _user_id.get() or "-",
            "msg": record.getMessage(),
        }
        if record.exc_info:
            # Num campo, não solto: um traceback multilinha quebra "uma
            # linha, um evento", e o worker é onde mais aparece
            # traceback (falha de provedor, timeout de turno).
            linha["exc"] = self.formatException(record.exc_info)
        return json.dumps(linha, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Mesmo envelope JSON do core-api, campo a campo (§12).

    Idêntico de propósito: é o que permite `grep` de um trace_id nos
    logs dos dois serviços e ler o resultado como uma sequência só.

    O filtro vai nos **handlers**, não no logger. Um `logging.Filter`
    num logger só roda para registros emitidos naquele logger; os que
    sobem por propagação dos filhos — e `arq.worker` é um deles, com o
    log de início e fim de cada job — chegam ao handler sem passar
    pelos filtros do raiz. Com o filtro no lugar errado, todo log do
    `arq` morreu com `ValueError: Formatting field not found in
    record: 'trace_id'`.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(TraceFormatter())
    handler.addFilter(TraceFilter())

    raiz = logging.getLogger()
    # Dois handlers duplicariam cada linha, e `basicConfig` não
    # substitui o que já existe.
    for antigo in list(raiz.handlers):
        raiz.removeHandler(antigo)
    raiz.addHandler(handler)
    raiz.setLevel(level)

    # Vale para a API interna (`app.main`), que roda sob uvicorn: ele
    # aplica o LOGGING_CONFIG próprio ao subir e dá a `uvicorn.access`
    # um handler com `propagate: False`, o que deixaria a linha de
    # acesso fora do envelope JSON e sem trace.
    for nome in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(nome)
        for antigo in list(lg.handlers):
            lg.removeHandler(antigo)
        lg.propagate = True


class TraceMiddleware:
    """Para a superfície HTTP interna do worker (`app.main`).

    O worker tem dois processos a partir da mesma imagem: o consumidor
    da fila (`app.worker`) e esta API interna. O consumidor entra pelo
    `bind()`; os endpoints internos entram por aqui, porque quem os
    chama é o `core-api`, com um header.

    ASGI puro pelo mesmo motivo do core-api: `BaseHTTPMiddleware` roda o
    endpoint em outra task e o `ContextVar` não atravessa de forma
    confiável.
    """

    def __init__(self, app) -> None:  # noqa: ANN001 - ASGIApp, sem starlette aqui
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        bruto = next(
            (
                v.decode("latin-1")
                for k, v in scope.get("headers", [])
                if k.decode("latin-1").lower() == HEADER.lower()
            ),
            None,
        )
        _trace_id.set(sanitize(bruto) or new_trace_id())
        _user_id.set(None)
        await self.app(scope, receive, send)
