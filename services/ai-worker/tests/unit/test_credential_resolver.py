"""Testes da resolução de credencial e da cota (§3451).

O que está sendo verificado é uma árvore de decisão com consequência
financeira: quem paga pelo turno, e quando o turno é recusado. Por isso
os testes cobrem a precedência inteira, e não só o caminho felizes.

Banco falso, não container: as consultas são três (credencial, limite,
soma do mês) e o que importa aqui é a DECISÃO sobre as respostas delas,
não o SQL. O SQL é exercitado contra o Postgres de verdade em
`infra/smoke-m1.sh`.

O fake reconhece a consulta pelo trecho de SQL, o que é frágil de
propósito: se alguém reescrever uma das consultas, o teste falha em vez
de passar silenciosamente contra uma pergunta que não é mais feita.
"""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app import credential_resolver as cr  # noqa: E402
from app.config import Settings  # noqa: E402
from app.credential_resolver import (  # noqa: E402
    NoCredential,
    QuotaExceeded,
    resolve,
)


class FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def first(self):
        return self._row


class FakeDb:
    """Responde às três consultas do resolver pelo texto do SQL.

    `cred_row` é o que a busca em `core.llm_credentials` devolve (None =
    o usuário não tem chave própria), `quota` é
    `users.monthly_quota_usd` e `usado` é a soma do mês.
    """

    def __init__(self, *, cred_row=None, quota=None, usado=Decimal(0)) -> None:
        self.cred_row = cred_row
        self.quota = quota
        self.usado = usado
        self.consultas: list[str] = []

    async def execute(self, clause, params=None):
        sql = str(clause)
        self.consultas.append(sql)
        if "llm_credentials" in sql:
            return FakeResult(self.cred_row)
        if "monthly_quota_usd" in sql:
            return FakeResult(None if self.quota is None else (self.quota,))
        if "turn_usage" in sql:
            return FakeResult((self.usado,))
        raise AssertionError(f"consulta inesperada: {sql}")


def settings_com(**over) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://x@localhost/x",
        "redis_url": "redis://localhost:6379/0",
        "master_key": "k" * 48,
        "service_jwt_secret": "s" * 48,
    }
    return Settings(**{**base, **over})  # type: ignore[arg-type]


@pytest.fixture
def com_instancia(monkeypatch: pytest.MonkeyPatch):
    """Instância com chave configurada (o modo de §3451 ligado)."""
    s = settings_com(
        instance_llm_api_key="chave-do-operador",
        instance_llm_provider="deepseek",
        instance_llm_model="deepseek-chat",
    )
    monkeypatch.setattr(cr, "get_settings", lambda: s)
    return s


@pytest.fixture
def sem_instancia(monkeypatch: pytest.MonkeyPatch):
    s = settings_com(instance_llm_api_key="")
    monkeypatch.setattr(cr, "get_settings", lambda: s)
    return s


@pytest.fixture
def decifra_fake(monkeypatch: pytest.MonkeyPatch):
    """Evita precisar de uma chave cifrada de verdade nos testes.

    O que a decifragem faz está coberto em `test_crypto.py`; aqui só
    importa que a chave do usuário chegue ao resultado.
    """

    class FakeBox:
        def __init__(self, _key) -> None:
            pass

        def decrypt(self, blob, _ctx) -> str:
            return "chave-decifrada-do-usuario"

    monkeypatch.setattr(cr, "SecretBox", FakeBox)


# ---------------------------------------------------------------
# Precedência
# ---------------------------------------------------------------
async def test_chave_do_usuario_vence_a_da_instancia(
    com_instancia, decifra_fake
) -> None:
    """Quem colou a própria chave usa a dele, mesmo havendo a da casa.

    Ignorar a chave do usuário gastaria o dinheiro do operador contra a
    vontade explícita dele, e tornaria a tela de Configurações uma
    mentira.
    """
    db = FakeDb(cred_row=("mock", b"cifrado", "mock-chat", None))
    cred = await resolve(db, uuid4(), uuid4())

    assert cred.source == "user"
    assert cred.api_key == "chave-decifrada-do-usuario"
    assert cred.provider_key == "mock"
    assert not cred.is_instance


async def test_sem_chave_propria_usa_a_da_instancia(com_instancia) -> None:
    """O caso que justifica o modo existir: conta nova, zero configuração."""
    db = FakeDb(cred_row=None, quota=Decimal("2.00"), usado=Decimal("0.10"))
    cred = await resolve(db, uuid4(), uuid4())

    assert cred.source == "instance"
    assert cred.api_key == "chave-do-operador"
    assert cred.provider_key == "deepseek"
    assert cred.model == "deepseek-chat"
    assert cred.is_instance


async def test_sem_chave_nenhuma_levanta(sem_instancia) -> None:
    db = FakeDb(cred_row=None)
    with pytest.raises(NoCredential):
        await resolve(db, uuid4(), uuid4())


async def test_chave_do_usuario_nao_consulta_cota(
    com_instancia, decifra_fake
) -> None:
    """Quem paga a própria conta não tem limite, e a cota não é nem lida.

    Verificado pelas consultas feitas, e não só pelo resultado: se a
    cota fosse consultada e ignorada, o teste de valor passaria e um
    refatoramento futuro poderia começar a aplicá-la sem ninguém notar.
    """
    db = FakeDb(cred_row=("mock", b"cifrado", None, None))
    await resolve(db, uuid4(), uuid4())

    assert not any("turn_usage" in c for c in db.consultas)
    assert not any("monthly_quota_usd" in c for c in db.consultas)


# ---------------------------------------------------------------
# Cota
# ---------------------------------------------------------------
async def test_dentro_da_cota_passa(com_instancia) -> None:
    db = FakeDb(cred_row=None, quota=Decimal("2.00"), usado=Decimal("1.99"))
    cred = await resolve(db, uuid4(), uuid4())
    assert cred.is_instance


async def test_cota_esgotada_levanta_com_os_numeros(com_instancia) -> None:
    """A exceção carrega usado e limite porque a mensagem precisa deles.

    "Cota excedida" não é acionável; "você usou os US$ 2,00 deste mês,
    cole sua chave para continuar" é (§5.9.13).
    """
    db = FakeDb(cred_row=None, quota=Decimal("2.00"), usado=Decimal("2.00"))
    with pytest.raises(QuotaExceeded) as erro:
        await resolve(db, uuid4(), uuid4())

    assert erro.value.limit_usd == Decimal("2.00")
    assert erro.value.used_usd == Decimal("2.00")


async def test_cota_e_limite_inclusivo(com_instancia) -> None:
    """Gastou exatamente o limite = esgotado.

    `>=` e não `>`: com `>`, um limite de 2.00 deixaria passar um turno
    começando em 2.00 e terminando em 2.40 — o limite seria sempre
    ultrapassado por um turno, por construção.
    """
    db = FakeDb(cred_row=None, quota=Decimal("2.00"), usado=Decimal("2.0001"))
    with pytest.raises(QuotaExceeded):
        await resolve(db, uuid4(), uuid4())


@pytest.mark.parametrize("ilimitado", [None, Decimal(0)])
async def test_cota_nula_ou_zero_e_ilimitada(com_instancia, ilimitado) -> None:
    """NULL e 0 significam "sem limite", não "nenhum uso permitido".

    É assim que liberar uma conta específica — a do operador — não
    depende de adivinhar um teto que nunca seja atingido.
    """
    db = FakeDb(
        cred_row=None, quota=ilimitado, usado=Decimal("999999.99")
    )
    cred = await resolve(db, uuid4(), uuid4())
    assert cred.is_instance


async def test_cota_ilimitada_nao_soma_o_mes(com_instancia) -> None:
    """Sem limite, a soma é trabalho jogado fora — e ela roda no caminho
    crítico de todo turno."""
    db = FakeDb(cred_row=None, quota=None)
    await resolve(db, uuid4(), uuid4())
    assert not any("turn_usage" in c for c in db.consultas)


# ---------------------------------------------------------------
# A consulta da cota, e o que ela filtra
# ---------------------------------------------------------------
async def test_soma_do_mes_ignora_gasto_da_chave_do_usuario(
    com_instancia,
) -> None:
    """A consulta precisa filtrar `used_instance_key`.

    Sem esse filtro, quem colou a própria chave veria a cota do operador
    cair por gasto que ele mesmo pagou — e bastaria usar a chave própria
    por um mês para ficar sem cota ao voltar.
    """
    db = FakeDb(cred_row=None, quota=Decimal("2.00"), usado=Decimal(0))
    await resolve(db, uuid4(), uuid4())

    consulta = next(c for c in db.consultas if "turn_usage" in c)
    assert "used_instance_key = true" in consulta
    # Mês de calendário, que é o que alguém entende ao ler "deste mês" e
    # o que vai bater com uma fatura, no dia em que houver.
    assert "date_trunc('month'" in consulta


async def test_modelo_vazio_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """String vazia na variável de ambiente significa "o default do
    adaptador". Passar "" adiante faria o provedor pedir um modelo
    chamado "".
    """
    s = settings_com(
        instance_llm_api_key="chave",
        instance_llm_provider="mock",
        instance_llm_model="",
        instance_llm_base_url="",
    )
    monkeypatch.setattr(cr, "get_settings", lambda: s)

    db = FakeDb(cred_row=None, quota=None)
    cred = await resolve(db, uuid4(), uuid4())
    assert cred.model is None
    assert cred.base_url is None


async def test_chave_com_espacos_e_tratada_como_ausente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uma variável de ambiente com espaço em branco é um erro de
    configuração, e tratá-la como chave válida produziria 401 do
    provedor em vez da mensagem clara de "configure uma chave"."""
    s = settings_com(instance_llm_api_key="   ")
    monkeypatch.setattr(cr, "get_settings", lambda: s)

    db = FakeDb(cred_row=None)
    with pytest.raises(NoCredential):
        await resolve(db, uuid4(), uuid4())
