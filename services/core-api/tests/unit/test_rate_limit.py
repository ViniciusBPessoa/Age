"""Testes do rate limit.

ARQUITETURA.md §7.0 e §9.1.

Estes testes rodam contra um **fake de Redis**, não contra o real: o que
está sendo verificado é a aritmética da janela deslizante e o
`Retry-After`, e para isso o relógio precisa ser controlável. Um teste
que dependesse do tempo de parede para expirar uma janela de 15 minutos
não roda em CI.

A integração com o Redis de verdade é coberta em `infra/smoke-ui.sh`,
contra o container.

O fake implementa só as cinco operações usadas, com a semântica do
Redis que importa aqui:
  - `zremrangebyscore` remove por faixa de score (inclusive nas pontas);
  - `zcard` conta;
  - `zadd` é idempotente por MEMBRO, não por score;
  - `zrange(0, 0, withscores=True)` devolve o de menor score;
  - o pipeline acumula e devolve os resultados em ordem.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("JWT_SIGNING_KEY", "j" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from fastapi import HTTPException  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.rate_limit import (  # noqa: E402
    Limit,
    allow,
    enforce,
    parse_limit,
    reset,
)


# ---------------------------------------------------------------
# Fake de Redis: só o necessário, com a semântica que importa
# ---------------------------------------------------------------
class FakePipeline:
    def __init__(self, store: dict[str, dict[str, float]]) -> None:
        self._store = store
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key: str, lo: float, hi: float) -> None:
        self._ops.append(("zrem", key, lo, hi))

    def zcard(self, key: str) -> None:
        self._ops.append(("zcard", key))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._ops.append(("zadd", key, mapping))

    def expire(self, key: str, seconds: int) -> None:
        self._ops.append(("expire", key, seconds))

    def zrange(
        self, key: str, start: int, stop: int, withscores: bool = False
    ) -> None:
        self._ops.append(("zrange", key, start, stop, withscores))

    async def execute(self) -> list:
        out: list = []
        for op in self._ops:
            kind = op[0]
            if kind == "zrem":
                _, key, lo, hi = op
                members = self._store.setdefault(key, {})
                for member in [m for m, sc in members.items() if lo <= sc <= hi]:
                    del members[member]
                out.append(None)
            elif kind == "zcard":
                out.append(len(self._store.setdefault(op[1], {})))
            elif kind == "zadd":
                _, key, mapping = op
                self._store.setdefault(key, {}).update(mapping)
                out.append(len(mapping))
            elif kind == "expire":
                out.append(True)
            elif kind == "zrange":
                _, key, start, stop, withscores = op
                items = sorted(
                    self._store.setdefault(key, {}).items(), key=lambda kv: kv[1]
                )
                sliced = items[start : (None if stop == -1 else stop + 1)]
                out.append(sliced if withscores else [m for m, _ in sliced])
        return out


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self.store)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


class BrokenRedis:
    """Redis fora do ar, para o caminho de degradação."""

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        raise ConnectionError("redis fora do ar")

    async def delete(self, key: str) -> int:
        raise ConnectionError("redis fora do ar")


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Relógio controlável.

    `time.time` é o que define a janela; `monotonic_ns` só compõe o
    membro único, mas precisa avançar ou duas tentativas no mesmo
    instante colidiriam no fake (e no Redis real).
    """
    # Alinhado a um múltiplo de 3600: é o que torna
    # `test_nao_deixa_passar_o_dobro_na_virada` honesto. Com um
    # instante arbitrário (1_000_000, que é 40s dentro do minuto), a
    # "virada" que o teste tenta cruzar cai no lugar errado e a rajada
    # acaba inteira numa janela só — o teste passaria mesmo com
    # contador por janela fixa, que é justamente o bug que ele existe
    # para pegar. Verificado sabotando o código.
    state = {"t": 1_000_000.0 - (1_000_000 % 3600), "n": 0}

    def fake_time() -> float:
        return state["t"]

    def fake_monotonic_ns() -> int:
        state["n"] += 1
        return state["n"]

    monkeypatch.setattr(rate_limit.time, "time", fake_time)
    monkeypatch.setattr(rate_limit.time, "monotonic_ns", fake_monotonic_ns)

    def advance(seconds: float) -> None:
        state["t"] += seconds

    return advance


# ---------------------------------------------------------------
# parse_limit
# ---------------------------------------------------------------
def test_parse_limit_le_formato_compacto() -> None:
    assert parse_limit("5/900") == Limit(times=5, seconds=900)


@pytest.mark.parametrize("ruim", ["", "5", "a/b", "5/", "/900", "0/60", "5/0"])
def test_parse_limit_recusa_formato_invalido(ruim: str) -> None:
    """Config errada falha ao ser lida, não silenciosamente liberada.

    Um limite inválido tratado como "sem limite" é pior que um erro: a
    salvaguarda sumiria sem ninguém notar.
    """
    with pytest.raises(ValueError):
        parse_limit(ruim)


# ---------------------------------------------------------------
# A aritmética da janela
# ---------------------------------------------------------------
async def test_permite_ate_o_limite_e_bloqueia_a_seguinte(
    fake_redis: FakeRedis, clock
) -> None:
    limite = Limit(times=3, seconds=60)

    for esperado_restante in (2, 1, 0):
        veredito = await allow("b", "s", limite)
        assert veredito.allowed
        assert veredito.remaining == esperado_restante

    quarta = await allow("b", "s", limite)
    assert not quarta.allowed
    assert quarta.remaining == 0


async def test_sujeitos_diferentes_nao_se_misturam(
    fake_redis: FakeRedis, clock
) -> None:
    limite = Limit(times=1, seconds=60)
    assert (await allow("login", "ana@x.com", limite)).allowed
    assert not (await allow("login", "ana@x.com", limite)).allowed
    # Outro e-mail começa do zero.
    assert (await allow("login", "bruno@x.com", limite)).allowed


async def test_buckets_diferentes_nao_se_misturam(
    fake_redis: FakeRedis, clock
) -> None:
    """O mesmo IP sob dois limites distintos conta separado.

    Importa porque login e registro limitam o mesmo IP com números
    diferentes; se compartilhassem chave, o menor venceria os dois.
    """
    limite = Limit(times=1, seconds=60)
    assert (await allow("login:ip", "1.2.3.4", limite)).allowed
    assert (await allow("register:ip", "1.2.3.4", limite)).allowed


async def test_janela_desliza_liberando_vaga_gradualmente(
    fake_redis: FakeRedis, clock
) -> None:
    """O ponto de ser deslizante: a vaga volta quando a tentativa
    ANTIGA sai da janela, não de uma vez na virada do minuto.

    Sem nenhuma tentativa bloqueada no meio, para isolar o
    deslizamento — `test_tentativa_bloqueada_tambem_conta` cobre o
    efeito de insistir.
    """
    limite = Limit(times=2, seconds=60)

    await allow("b", "s", limite)  # t=0
    clock(30)
    await allow("b", "s", limite)  # t=30, agora cheio

    clock(31)  # t=61: a primeira (t=0) saiu da janela
    assert (await allow("b", "s", limite)).allowed

    # Mas a de t=30 ainda está dentro, então não liberou tudo.
    assert not (await allow("b", "s", limite)).allowed


async def test_tentativa_bloqueada_tambem_conta(
    fake_redis: FakeRedis, clock
) -> None:
    """Insistir depois do `429` empurra a janela para frente.

    Deliberado: o atacante é quem continua tentando, e o cliente
    legítimo respeita o `Retry-After` sem gastar tentativa. A primeira
    versão deste arquivo supunha o contrário e o teste de deslizamento
    falhou — a falha estava na suposição, não no código.
    """
    limite = Limit(times=1, seconds=60)

    await allow("b", "s", limite)  # t=0, aceita
    clock(30)
    assert not (await allow("b", "s", limite)).allowed  # t=30, bloqueada

    clock(31)  # t=61: a de t=0 saiu, mas a bloqueada de t=30 ficou
    ainda_bloqueado = await allow("b", "s", limite)
    assert not ainda_bloqueado.allowed, (
        "a tentativa bloqueada precisa contar, senão insistir sai de "
        "graça e a janela nunca se estende para quem martela"
    )

    # E a de t=61 — também bloqueada — ficou registrada igual. Quem
    # desiste de insistir é liberado 60s depois da ÚLTIMA tentativa, não
    # 60s depois da primeira: é exatamente o efeito pretendido.
    clock(61)  # t=122, nada mais na janela
    assert (await allow("b", "s", limite)).allowed


async def test_nao_deixa_passar_o_dobro_na_virada(
    fake_redis: FakeRedis, clock
) -> None:
    """A regressão que justifica janela deslizante em vez de contador.

    Com contador por janela fixa, 3 tentativas no fim de uma janela e 3
    no começo da seguinte passam — 6 em dois segundos, com limite de 3.
    É a rajada que importa para força bruta. Aqui a segunda rajada é
    bloqueada.
    """
    limite = Limit(times=3, seconds=60)

    # O relógio do fixture está alinhado à janela, então t=59 é o último
    # segundo da primeira janela fixa e t=61 já é a segunda. Um contador
    # por janela fixa zera entre as duas; a janela deslizante não.
    clock(59)
    for _ in range(3):
        assert (await allow("b", "s", limite)).allowed

    clock(2)  # cruzou a virada de uma janela fixa
    for tentativa in range(3):
        veredito = await allow("b", "s", limite)
        assert not veredito.allowed, (
            f"tentativa {tentativa + 1} passou na virada: é o bug que "
            "a janela deslizante existe para não ter"
        )


# ---------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------
async def test_retry_after_reflete_quando_a_vaga_abre(
    fake_redis: FakeRedis, clock
) -> None:
    limite = Limit(times=1, seconds=60)
    await allow("b", "s", limite)  # t=0

    clock(20)
    veredito = await allow("b", "s", limite)
    assert not veredito.allowed
    # A tentativa de t=0 sai da janela em t=60; agora é t=20.
    assert veredito.retry_after == 40


async def test_retry_after_nunca_e_zero_quando_bloqueado(
    fake_redis: FakeRedis, clock
) -> None:
    """`Retry-After: 0` convidaria a repetir na hora — o oposto do
    objetivo. Arredondamento para cima, com piso de 1.
    """
    limite = Limit(times=1, seconds=60)
    await allow("b", "s", limite)

    clock(59.5)  # faltam 0.5s para a vaga abrir
    veredito = await allow("b", "s", limite)
    assert not veredito.allowed
    assert veredito.retry_after >= 1


async def test_retry_after_nao_excede_a_janela(
    fake_redis: FakeRedis, clock
) -> None:
    limite = Limit(times=1, seconds=900)
    await allow("b", "s", limite)
    veredito = await allow("b", "s", limite)
    assert 1 <= veredito.retry_after <= 900


# ---------------------------------------------------------------
# reset
# ---------------------------------------------------------------
async def test_reset_zera_a_contagem(fake_redis: FakeRedis, clock) -> None:
    limite = Limit(times=1, seconds=60)
    await allow("login:email", "ana@x.com", limite)
    assert not (await allow("login:email", "ana@x.com", limite)).allowed

    await reset("login:email", "ana@x.com")
    assert (await allow("login:email", "ana@x.com", limite)).allowed


# ---------------------------------------------------------------
# Degradação: Redis fora do ar
# ---------------------------------------------------------------
async def test_redis_fora_do_ar_libera_em_vez_de_barrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Salvaguarda indisponível não vira porta fechada.

    Transformar a queda do Redis em login indisponível trocaria um risco
    por uma falha garantida.
    """
    monkeypatch.setattr(rate_limit, "get_redis", lambda: BrokenRedis())
    veredito = await allow("b", "s", Limit(times=1, seconds=60))
    assert veredito.allowed


async def test_redis_fora_do_ar_registra_o_motivo(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Liberar em silêncio é o que não serve: sem log, ninguém nota que
    o limite parou de valer.
    """
    monkeypatch.setattr(rate_limit, "get_redis", lambda: BrokenRedis())
    with caplog.at_level("ERROR", logger="core-api.rate_limit"):
        await allow("login:email", "ana@x.com", Limit(times=1, seconds=60))
    assert "rate limit indisponível" in caplog.text
    assert "ConnectionError" in caplog.text


async def test_reset_com_redis_fora_do_ar_nao_propaga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`reset` roda no caminho de SUCESSO do login.

    Se ele estourasse com o Redis fora, a queda do Redis derrubaria o
    login que já tinha dado certo — e a senha certa viraria erro.
    """
    monkeypatch.setattr(rate_limit, "get_redis", lambda: BrokenRedis())
    await reset("login:email", "ana@x.com")  # não levanta


# ---------------------------------------------------------------
# enforce: a interface que as rotas usam
# ---------------------------------------------------------------
async def test_enforce_levanta_429_com_retry_after(
    fake_redis: FakeRedis, clock
) -> None:
    await enforce("b", "s", "1/60", "devagar")

    with pytest.raises(HTTPException) as erro:
        await enforce("b", "s", "1/60", "devagar")

    exc = erro.value
    assert exc.status_code == 429
    assert exc.detail["error"]["code"] == "rate_limited"
    assert exc.detail["error"]["message"] == "devagar"
    # O header é o que o cliente HTTP respeita automaticamente.
    assert exc.headers is not None
    assert int(exc.headers["Retry-After"]) >= 1
    # E o mesmo número no corpo, para o front poder dizer quanto falta.
    assert exc.detail["error"]["details"]["retry_after_seconds"] >= 1


async def test_enforce_desligado_nao_consulta_o_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`rate_limit_enabled=false` é como o smoke e o CI rodam dezenas de
    logins de um IP só sem desviar do caminho real da rota.
    """
    from app.core.config import Settings, get_settings

    base = get_settings()
    desligado = Settings(**{**base.model_dump(), "rate_limit_enabled": False})
    monkeypatch.setattr(rate_limit, "get_settings", lambda: desligado)
    # Redis quebrado de propósito: se `enforce` o consultasse, estouraria
    # ou registraria erro. Aqui nem deve chegar lá.
    monkeypatch.setattr(rate_limit, "get_redis", lambda: BrokenRedis())

    for _ in range(50):
        await enforce("b", "s", "1/60", "devagar")
