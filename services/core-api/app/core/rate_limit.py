"""Rate limit por janela deslizante, no Redis.

ARQUITETURA.md §7.0 ("rate limit por usuário, com `429` e
`Retry-After`; limite separado e mais baixo para rotas que disparam
LLM") e §9.1 ("por usuário e por IP").

**Por que janela deslizante e não contador por janela fixa.** O contador
fixo (`INCR` numa chave que expira a cada minuto) deixa passar o dobro
do limite na virada: 10 tentativas às 11:59:59 e 10 às 12:00:01 são 20
em dois segundos, e para força bruta de senha é justamente a rajada que
importa. Aqui cada tentativa é um membro de um `ZSET` com o timestamp
como score; a janela é consultada por intervalo, então não existe
virada.

O custo é um `ZSET` por sujeito em vez de um inteiro — alguns bytes por
chave, com expiração automática. Para o volume deste produto é
irrelevante, e o `ZREMRANGEBYSCORE` no início de cada chamada mantém o
conjunto do tamanho do limite, não do tráfego.

**Por que num pipeline e não em chamadas soltas.** As quatro operações
(limpar o que saiu da janela, contar, registrar, renovar o TTL) precisam
ser uma unidade: entre contar e registrar, duas requisições paralelas
leriam a mesma contagem e as duas passariam. `pipeline(transaction=True)`
embala num `MULTI/EXEC`, que o Redis executa sem intercalar.

**Falha do Redis não fecha a porta.** Se o Redis cair, `allow()` devolve
permissão em vez de erro: o rate limit é uma salvaguarda, e transformar
a queda dele em indisponibilidade do login seria trocar um risco por uma
falha garantida. A decisão é registrada em log — perder o limite em
silêncio é o que não serve.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.redis import get_redis

log = logging.getLogger("core-api.rate_limit")


@dataclass(frozen=True)
class Limit:
    """Quantas vezes (`times`) em quantos segundos (`seconds`)."""

    times: int
    seconds: int

    def __post_init__(self) -> None:
        if self.times < 1 or self.seconds < 1:
            raise ValueError("limite precisa de times>=1 e seconds>=1")


@dataclass(frozen=True)
class Verdict:
    """Resultado de uma consulta ao limite.

    `retry_after` é inteiro e nunca zero quando bloqueado: o header
    `Retry-After` é em segundos, e "0" convidaria o cliente a repetir
    imediatamente — exatamente o que o limite quer evitar.
    """

    allowed: bool
    remaining: int
    retry_after: int


async def allow(bucket: str, subject: str, limit: Limit) -> Verdict:
    """Registra uma tentativa e diz se ela cabe no limite.

    `bucket` é o grupo ("login", "llm"), `subject` é quem está sendo
    limitado (um id de usuário, um IP, um e-mail). Os dois entram na
    chave, então limites diferentes sobre o mesmo sujeito não se
    misturam.

    **A tentativa bloqueada também é registrada**, e isso é deliberado:
    quem insiste depois do `429` empurra a janela para frente e demora
    mais a ser liberado. É o comportamento desejado contra força bruta,
    porque o atacante é justamente quem continua tentando — e o
    cliente legítimo respeita o `Retry-After` e não gasta tentativa.

    A consequência prática é que o `Retry-After` é medido a partir da
    tentativa mais antiga ainda na janela, não a partir da primeira que
    foi aceita.
    """
    key = f"rl:{bucket}:{subject}"
    now_ms = int(time.time() * 1000)
    window_ms = limit.seconds * 1000
    cutoff = now_ms - window_ms

    try:
        redis = get_redis()
        pipe = redis.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        # Membro único por tentativa: duas no mesmo milissegundo têm o
        # mesmo score, e com membro igual a segunda sobrescreveria a
        # primeira em vez de contar.
        pipe.zadd(key, {f"{now_ms}-{time.monotonic_ns()}": now_ms})
        pipe.expire(key, limit.seconds + 1)
        # Para o `Retry-After`: quando bloqueado, o que libera vaga é a
        # tentativa mais ANTIGA da janela sair dela.
        pipe.zrange(key, 0, 0, withscores=True)
        _, used, _, _, oldest = await pipe.execute()
    except Exception as exc:
        # Ver o docstring do módulo: salvaguarda indisponível não vira
        # porta fechada, mas também não vira silêncio.
        log.error(
            "rate limit indisponível (bucket=%s); liberando: %s: %s",
            bucket,
            type(exc).__name__,
            exc,
        )
        return Verdict(allowed=True, remaining=limit.times, retry_after=0)

    # `used` é a contagem ANTES do `zadd` desta tentativa, então a
    # tentativa atual é a de número `used + 1`.
    if used < limit.times:
        return Verdict(
            allowed=True, remaining=limit.times - used - 1, retry_after=0
        )

    retry_after = limit.seconds
    if oldest:
        oldest_ms = int(oldest[0][1])
        # Ceil, não round: arredondar para baixo devolveria um horário
        # em que a vaga ainda não existe, e a repetição falharia de novo.
        restante = (oldest_ms + window_ms - now_ms + 999) // 1000
        retry_after = max(1, min(limit.seconds, restante))

    return Verdict(allowed=False, remaining=0, retry_after=retry_after)


async def reset(bucket: str, subject: str) -> None:
    """Zera a contagem. Usado no sucesso do login e nos testes.

    Sem isto, dez tentativas certas seguidas (trocar de dispositivo,
    testar a conta) bloqueariam como se fossem ataque. Quem acertou a
    senha provou que não é força bruta.
    """
    try:
        await get_redis().delete(f"rl:{bucket}:{subject}")
    except Exception as exc:
        log.warning("falha ao limpar rate limit %s/%s: %s", bucket, subject, exc)


def parse_limit(spec: str) -> Limit:
    """Lê `"5/900"` como 5 vezes em 900 segundos.

    Formato compacto porque vem de variável de ambiente, onde um objeto
    aninhado seria pior de escrever e de ler.
    """
    try:
        times, seconds = spec.split("/", 1)
        return Limit(times=int(times), seconds=int(seconds))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f'limite inválido: {spec!r}; esperado "vezes/segundos", ex. "5/900"'
        ) from exc


def too_many_requests(verdict: Verdict, message: str) -> HTTPException:
    """O `429` no formato de erro de §7.3, com `Retry-After`.

    `code` é estável (`rate_limited`) para o front mapear; `message` é o
    fallback em pt-BR e diz quanto esperar, porque "tente mais tarde"
    sem número é o tipo de aviso que não ajuda ninguém.
    """
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": {
                "code": "rate_limited",
                "message": message,
                "details": {"retry_after_seconds": verdict.retry_after},
            }
        },
        headers={"Retry-After": str(verdict.retry_after)},
    )


async def enforce(
    bucket: str, subject: str, spec: str, message: str
) -> None:
    """Consulta o limite e levanta `429` se estourou.

    É o único ponto que as rotas chamam. Respeita
    `rate_limit_enabled=false`, que é como o smoke test e o CI rodam
    dezenas de logins de um IP só sem ter que desviar do caminho real.
    """
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return

    verdict = await allow(bucket, subject, parse_limit(spec))
    if not verdict.allowed:
        # Log em `warning`: estouro de limite é sinal operacional, e
        # sem registro não há como notar um ataque em curso. O sujeito
        # é truncado para não gravar e-mail inteiro no log.
        log.warning(
            "rate limit estourado bucket=%s subject=%s retry_after=%ss",
            bucket,
            subject[:12],
            verdict.retry_after,
        )
        raise too_many_requests(verdict, message)
