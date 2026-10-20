"""A rota que o painel de uso lê.

Contra Postgres de verdade porque tudo que importa aqui é SQL:
`date_trunc` com fuso, `generate_series` preenchendo vão, e `SUM` de
coluna toda nula devolvendo `NULL` em vez de zero.

O agrupamento por fuso não é purismo. Medido na conta real: 15 dos 60
turnos do usuário caem num dia em São Paulo e no SEGUINTE em UTC,
porque ele usa o Age à noite. Um gráfico agrupado em UTC mostraria
esses turnos no dia errado.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="precisa do Postgres do compose",
)


@pytest.fixture
async def db():
    from app.core.db import dispose_engine, user_session

    await dispose_engine()
    yield user_session
    await dispose_engine()


def _engine_dono():
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.getenv("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
    return create_async_engine(url)


async def _usuario(tz: str = "America/Sao_Paulo") -> uuid.UUID:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO core.users (id, email, password_hash, "
                    "timezone) VALUES (:u, :e, 'x', :tz)"
                ),
                {"u": str(user_id), "e": f"rota-{user_id}@age.dev.br", "tz": tz},
            )
    finally:
        await engine.dispose()
    return user_id


async def _limpar(user_id: uuid.UUID) -> None:
    from sqlalchemy import text

    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.user_id', :u, true)"),
                {"u": str(user_id)},
            )
            for tabela in (
                "core.usage_log",
                "ai.turn_usage",
                "core.user_preferences",
            ):
                await conn.execute(
                    text(f"DELETE FROM {tabela} WHERE user_id = :u"),
                    {"u": str(user_id)},
                )
            await conn.execute(
                text("DELETE FROM core.users WHERE id = :u"),
                {"u": str(user_id)},
            )
    finally:
        await engine.dispose()


async def _uso(
    user_id: uuid.UUID,
    quando: datetime,
    *,
    pt: int = 1000,
    ct: int = 100,
    custo: float | None = 0.001,
    cached: int | None = None,
    model: str = "deepseek-flash",
) -> None:
    """Uma linha de uso, pela engine do dono (§2.3.2: `ai` é do worker)."""
    from sqlalchemy import text

    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.user_id', :u, true)"),
                {"u": str(user_id)},
            )
            await conn.execute(
                text(
                    "INSERT INTO ai.turn_usage (turn_id, user_id, "
                    "provider_key, model, prompt_tokens, completion_tokens, "
                    "cached_prompt_tokens, estimated_cost_usd, created_at) "
                    "VALUES (NULL, :u, 'deepseek', :m, :pt, :ct, :cached, "
                    ":custo, :w)"
                ),
                {
                    "u": str(user_id),
                    "m": model,
                    "pt": pt,
                    "ct": ct,
                    "cached": cached,
                    "custo": custo,
                    "w": quando,
                },
            )
    finally:
        await engine.dispose()


async def _chamar(db, user_id, **kwargs):
    """Chama a rota sem HTTP, injetando o usuário."""
    from types import SimpleNamespace

    from app.api.usage import usage_summary

    async with db(user_id) as conn:
        return await usage_summary(
            user=SimpleNamespace(id=user_id), db=conn, **kwargs
        )


async def test_agrupa_no_fuso_da_conta(db) -> None:
    """Sabotagem: `date_trunc('day', created_at)` sem `AT TIME ZONE`.

    23:30 em São Paulo é 02:30 UTC do dia SEGUINTE. Agrupado em UTC, o
    uso aparece no dia 21; no fuso da conta, no dia 20 — que é quando a
    pessoa estava usando o Age.

    Medido na conta real: 15 dos 60 turnos do usuário divergem assim.
    """
    user_id = await _usuario("America/Sao_Paulo")
    try:
        # 2026-10-20 23:30 em SP = 2026-10-21 02:30 UTC
        await _uso(user_id, datetime(2026, 10, 21, 2, 30, tzinfo=UTC))

        r = await _chamar(
            db,
            user_id,
            start=datetime(2026, 10, 19, tzinfo=UTC),
            end=datetime(2026, 10, 22, tzinfo=UTC),
            bucket="day",
        )

        com_uso = [b for b in r.buckets if b.prompt_tokens > 0]
        assert len(com_uso) == 1
        assert com_uso[0].day == "2026-10-20", (
            "o uso das 23:30 em SP pertence ao dia 20, não ao 21"
        )
    finally:
        await _limpar(user_id)


async def test_dia_sem_uso_vira_bucket_zerado(db) -> None:
    """Sabotagem: remover o `generate_series` / o LEFT JOIN.

    Sem os dias vazios o gráfico comprime os vãos, e um fim de semana
    parado parece não ter existido — sete barras seguidas mentiriam
    sobre o ritmo da semana.
    """
    user_id = await _usuario()
    try:
        await _uso(user_id, datetime(2026, 10, 15, 15, tzinfo=UTC))

        r = await _chamar(
            db,
            user_id,
            start=datetime(2026, 10, 13, tzinfo=UTC),
            end=datetime(2026, 10, 20, tzinfo=UTC),
            bucket="day",
        )

        assert len(r.buckets) == 8, "um ponto por dia do intervalo"
        vazios = [b for b in r.buckets if b.prompt_tokens == 0]
        assert len(vazios) == 7
    finally:
        await _limpar(user_id)


async def test_dia_sem_uso_tem_custo_none_e_nao_zero(db) -> None:
    """Sabotagem: `COALESCE(custo, 0)` na série.

    "Custo zero" e "não houve uso" desenham a mesma barra mas são fatos
    diferentes, e só o primeiro afirma gratuidade. A distinção percorre
    a feature inteira desde `estimate_cost_usd`.
    """
    user_id = await _usuario()
    try:
        await _uso(user_id, datetime(2026, 10, 15, 15, tzinfo=UTC))

        r = await _chamar(
            db,
            user_id,
            start=datetime(2026, 10, 13, tzinfo=UTC),
            end=datetime(2026, 10, 20, tzinfo=UTC),
            bucket="day",
        )

        vazio = next(b for b in r.buckets if b.prompt_tokens == 0)
        assert vazio.estimated_cost_usd is None
        com_uso = next(b for b in r.buckets if b.prompt_tokens > 0)
        assert com_uso.estimated_cost_usd == pytest.approx(0.001)
    finally:
        await _limpar(user_id)


async def test_periodo_sem_uso_nenhum_nao_quebra(db) -> None:
    """Conta nova abrindo o painel pela primeira vez.

    Sabotagem: assumir que `totais` tem linha, ou somar `None`. O
    painel de quem nunca mandou mensagem é o primeiro que alguém vê.
    """
    user_id = await _usuario()
    try:
        r = await _chamar(db, user_id, bucket="day")

        assert r.turns == 0
        assert r.total_tokens == 0
        assert r.estimated_cost_usd is None
        assert r.by_model == []
        assert len(r.buckets) == 31
    finally:
        await _limpar(user_id)


async def test_quebra_por_modelo_soma_certo(db) -> None:
    """Sabotagem: agrupar só por `model`, ignorando o provedor.

    Dois provedores podem servir nomes de modelo iguais, e somá-los
    juntos atribuiria custo de um ao outro.
    """
    user_id = await _usuario()
    try:
        agora = datetime.now(UTC) - timedelta(days=1)
        await _uso(user_id, agora, pt=1000, custo=0.01, model="deepseek-flash")
        await _uso(user_id, agora, pt=2000, custo=0.02, model="deepseek-flash")
        await _uso(user_id, agora, pt=500, custo=0.05, model="deepseek-v4-pro")

        r = await _chamar(db, user_id, bucket="day")

        por_modelo = {m.model: m for m in r.by_model}
        assert por_modelo["deepseek-flash"].turns == 2
        assert por_modelo["deepseek-flash"].prompt_tokens == 3000
        assert por_modelo["deepseek-flash"].estimated_cost_usd == pytest.approx(
            0.03
        )
        assert por_modelo["deepseek-v4-pro"].turns == 1
        # Ordenado por tokens: o que mais consumiu vem primeiro.
        assert r.by_model[0].model == "deepseek-flash"
    finally:
        await _limpar(user_id)


async def test_custo_desconhecido_nao_vira_zero(db) -> None:
    """Modelo local, sem preço: `SUM` de coluna toda nula é `NULL`.

    Sabotagem: `COALESCE(SUM(estimated_cost_usd), 0)` nos totais. O
    painel diria "você gastou US$ 0,00" quando a verdade é "não sei
    quanto custou".
    """
    user_id = await _usuario()
    try:
        await _uso(user_id, datetime.now(UTC) - timedelta(days=1), custo=None)

        r = await _chamar(db, user_id, bucket="day")

        assert r.turns == 1
        assert r.total_tokens > 0
        assert r.estimated_cost_usd is None
        assert r.by_model[0].estimated_cost_usd is None
    finally:
        await _limpar(user_id)


async def test_custo_parcial_soma_o_que_se_conhece(db) -> None:
    """Metade com preço, metade sem: soma a parte conhecida.

    Sabotagem: devolver `None` se QUALQUER linha for nula. O usuário
    perderia o número inteiro por causa de um turno de modelo local.
    """
    user_id = await _usuario()
    try:
        agora = datetime.now(UTC) - timedelta(days=1)
        await _uso(user_id, agora, custo=0.01)
        await _uso(user_id, agora, custo=None)

        r = await _chamar(db, user_id, bucket="day")

        assert r.turns == 2
        assert r.estimated_cost_usd == pytest.approx(0.01)
    finally:
        await _limpar(user_id)


async def test_periodo_invertido_e_recusado(db) -> None:
    """Sabotagem: remover a validação.

    `generate_series` com passo positivo e fim antes do início devolve
    série vazia — o painel ficaria em branco sem dizer por quê.
    """
    from fastapi import HTTPException

    user_id = await _usuario()
    try:
        with pytest.raises(HTTPException) as exc:
            await _chamar(
                db,
                user_id,
                start=datetime(2026, 10, 20, tzinfo=UTC),
                end=datetime(2026, 10, 1, tzinfo=UTC),
            )
        assert exc.value.status_code == 422
    finally:
        await _limpar(user_id)


async def test_periodo_longo_demais_por_dia_e_recusado(db) -> None:
    """Sabotagem: remover o teto de pontos.

    A tela amarra o passo ao período, mas a rota é pública: dez anos por
    dia devolveria 3650 pontos e um gráfico ilegível.
    """
    from fastapi import HTTPException

    user_id = await _usuario()
    try:
        with pytest.raises(HTTPException) as exc:
            await _chamar(
                db,
                user_id,
                start=datetime(2016, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 1, tzinfo=UTC),
                bucket="day",
            )
        assert exc.value.status_code == 422
        assert "passo" in str(exc.value.detail)
    finally:
        await _limpar(user_id)


async def test_show_cost_respeita_o_teto_da_instancia(db) -> None:
    """Sabotagem: olhar só a preferência do usuário, ou só o teto.

    A preferência SÓ PODE DESLIGAR o que o operador deixou ligado. Sem
    o teto, virar a chave do operador não teria efeito em quem já tinha
    a preferência ligada — que é exatamente quem ele quer cobrir.
    """
    from sqlalchemy import text

    from app.core.config import get_settings

    user_id = await _usuario()
    try:
        async with db(user_id) as conn:
            await conn.execute(
                text(
                    "INSERT INTO core.user_preferences (user_id, "
                    "show_cost_usd) VALUES (:u, true)"
                ),
                {"u": str(user_id)},
            )

        r = await _chamar(db, user_id)
        assert r.show_cost is True

        # O operador vira a chave: some para todo mundo.
        settings = get_settings()
        original = settings.cost_display_enabled
        object.__setattr__(settings, "cost_display_enabled", False)
        try:
            r = await _chamar(db, user_id)
            assert r.show_cost is False, "o teto da instância manda"
        finally:
            object.__setattr__(settings, "cost_display_enabled", original)

        # Teto ligado, preferência desligada: também some.
        async with db(user_id) as conn:
            await conn.execute(
                text(
                    "UPDATE core.user_preferences SET show_cost_usd = false "
                    "WHERE user_id = :u"
                ),
                {"u": str(user_id)},
            )
        r = await _chamar(db, user_id)
        assert r.show_cost is False
    finally:
        await _limpar(user_id)


async def test_conta_sem_preferencias_mostra_o_custo(db) -> None:
    """Sabotagem: tratar ausência de linha como `false`.

    `user_preferences` só nasce no primeiro save, então a maioria das
    contas novas não tem linha. O default da coluna é `true`, e a
    função tem de concordar com o banco.
    """
    user_id = await _usuario()
    try:
        r = await _chamar(db, user_id)
        assert r.show_cost is True
    finally:
        await _limpar(user_id)
