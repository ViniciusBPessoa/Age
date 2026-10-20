"""O dreno de uso, contra Postgres de verdade.

Duas coisas só falham aqui, nunca numa suíte com banco falso:

1. **O `ON CONFLICT` com predicado parcial.** O Postgres só aceita
   inferência quando existe um índice único que casa a expressão
   INCLUINDO o predicado. Se o `WHERE` da query não for literalmente
   igual ao do índice, o erro é "there is no unique or exclusion
   constraint matching the ON CONFLICT specification" — e aí o dreno
   inteiro quebra, não só a parte de custo. É a lição que o cabeçalho
   da migration 0011 já registra: suíte com banco falso teria passado.

2. **A duplicata.** Dois turnos no mesmo `now()` com o mesmo modelo era
   o caso que o `NOT EXISTS` deixava passar, e havia uma linha assim
   neste banco quando a 0012 foi escrita.

Os dois lados são testados juntos de propósito: um teste que só prova
"não duplica" passaria com um código que nunca insere nada, e um que só
prova "insere" passaria com o código antigo. É a combinação que fecha.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

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


async def _usuario() -> uuid.UUID:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO core.users (id, email, password_hash, "
                    "timezone) VALUES (:u, :e, 'x', 'America/Sao_Paulo')"
                ),
                {"u": str(user_id), "e": f"uso-{user_id}@age.dev.br"},
            )
    finally:
        await engine.dispose()
    return user_id


async def _limpar(user_id: uuid.UUID) -> None:
    """Remove a conta e tudo que ela gerou.

    Roda contra o MESMO banco que o usuário usa, então conta de teste
    deixada para trás sujaria o relatório de custo dele.
    """
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
                "core.messages",
                "ai.turn_usage",
                "ai.turn_outputs",
                "ai.turns",
                "core.conversations",
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


async def _conversa(session_factory, user_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy import text

    cid = uuid.uuid4()
    async with session_factory(user_id) as conn:
        await conn.execute(
            text(
                "INSERT INTO core.conversations (id, user_id, title) "
                "VALUES (:c, :u, 'uso')"
            ),
            {"c": str(cid), "u": str(user_id)},
        )
    return cid


async def _turno_com_uso(
    session_factory,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    quando: datetime,
    custo: float | None,
    tokens: tuple[int, int] = (1000, 50),
    cached: int | None = None,
) -> uuid.UUID:
    """Um turno gravado como o worker grava: saída + uso.

    Pela engine do DONO, não pela de runtime: o core-api só tem SELECT
    em `ai.*` (§2.3.2) — quem escreve ali é o worker. Minha primeira
    versão usou `session_factory` e levou `permission denied for table
    turn_usage`, que é a fronteira de privilégio funcionando.
    """
    from sqlalchemy import text

    turn_id = uuid.uuid4()
    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.user_id', :u, true)"),
                {"u": str(user_id)},
            )
            await conn.execute(
                text(
                    "INSERT INTO ai.turns (id, user_id, conversation_id, "
                    "status, provider_key, model) VALUES (:t, :u, :c, "
                    "'done', 'deepseek', 'deepseek-flash')"
                ),
                {
                    "t": str(turn_id),
                    "u": str(user_id),
                    "c": str(conversation_id),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO ai.turn_outputs (turn_id, user_id, "
                    "conversation_id, role, content, sequence, "
                    "provider_key, model, created_at) VALUES (:t, :u, :c, "
                    "'assistant', 'ok', 0, 'deepseek', 'deepseek-flash', :w)"
                ),
                {
                    "t": str(turn_id),
                    "u": str(user_id),
                    "c": str(conversation_id),
                    "w": quando,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO ai.turn_usage (turn_id, user_id, "
                    "provider_key, model, prompt_tokens, "
                    "completion_tokens, cached_prompt_tokens, "
                    "estimated_cost_usd, created_at) VALUES (:t, :u, "
                    "'deepseek', 'deepseek-flash', :pt, :ct, :cached, "
                    ":cost, :w)"
                ),
                {
                    "t": str(turn_id),
                    "u": str(user_id),
                    "pt": tokens[0],
                    "ct": tokens[1],
                    "cached": cached,
                    "cost": custo,
                    "w": quando,
                },
            )
    finally:
        await engine.dispose()
    return turn_id


async def test_o_custo_chega_a_mensagem(db) -> None:
    """A cadeia que faz o custo sobreviver ao F5.

    Sabotagem que derruba:
      - tirar `u.estimated_cost_usd` do CTE `pending` → vem `None`;
      - tirar a coluna do INSERT em `core.messages` → idem;
      - tirar a coluna da migration → o INSERT estoura.

    Sem esta cadeia o custo só existiria no evento SSE, e recarregar a
    página o apagaria.
    """
    from sqlalchemy import text

    from app.services.turn_sync import drain_turn_outputs

    user_id = await _usuario()
    try:
        cid = await _conversa(db, user_id)
        await _turno_com_uso(
            db, user_id, cid, quando=datetime.now(UTC), custo=0.000354
        )

        async with db(user_id) as conn:
            copiadas = await drain_turn_outputs(conn, user_id, cid)
            assert copiadas == 1

            row = (
                await conn.execute(
                    text(
                        "SELECT prompt_tokens, completion_tokens, "
                        "estimated_cost_usd FROM core.messages "
                        "WHERE user_id = :u AND role = 'assistant'"
                    ),
                    {"u": str(user_id)},
                )
            ).first()

        assert row is not None
        assert row[0] == 1000
        assert row[1] == 50
        assert float(row[2]) == pytest.approx(0.000354)
    finally:
        await _limpar(user_id)


async def test_turno_com_a_mesma_chave_de_um_ja_drenado_nao_se_perde(
    db,
) -> None:
    """O turno que a dedupe heurística ENGOLIA.

    Sabotagem: voltar a dedupe para
    `NOT EXISTS (user_id, created_at, model)`. O segundo turno nunca
    entra, porque a checagem acha o primeiro — já copiado numa chamada
    anterior — e o conta como "este já está lá".

    Medi o comportamento antes de escrever o teste, e a minha primeira
    versão dele era VAZIA: eu criava os dois turnos e drenava uma vez
    só, e nesse arranjo o `NOT EXISTS` acerta, porque num
    `INSERT ... SELECT` ele lê o snapshot do início da instrução e não
    vê as linhas que a própria instrução insere — as duas entram. O
    teste passava com o código sabotado.

    O arranjo que expõe o defeito é com um dreno NO MEIO: o primeiro
    turno já está na trilha quando o segundo chega. Medido num cenário
    controlado: 1 linha e 100 tokens, onde o certo é 2 e 300. Ou seja, o
    defeito era SUBCONTAR, não duplicar — o oposto do que eu supunha ao
    escrever a migration, e a razão de a unique ser a resposta certa de
    qualquer forma.

    Mesmo `created_at` e mesmo modelo acontecem porque `now()` é
    constante dentro da transação do Postgres.
    """
    from sqlalchemy import text

    from app.services.turn_sync import drain_turn_outputs

    user_id = await _usuario()
    try:
        cid = await _conversa(db, user_id)
        instante = datetime.now(UTC)

        await _turno_com_uso(
            db, user_id, cid, quando=instante, custo=0.001, tokens=(100, 10)
        )
        async with db(user_id) as conn:
            await drain_turn_outputs(conn, user_id, cid)

        # Segundo turno, mesma chave (user_id, created_at, model).
        await _turno_com_uso(
            db, user_id, cid, quando=instante, custo=0.002, tokens=(200, 20)
        )
        async with db(user_id) as conn:
            await drain_turn_outputs(conn, user_id, cid)
            total = (
                await conn.execute(
                    text(
                        "SELECT count(*), sum(prompt_tokens) "
                        "FROM core.usage_log WHERE user_id = :u"
                    ),
                    {"u": str(user_id)},
                )
            ).first()

        assert total is not None
        assert total[0] == 2, "os dois turnos têm de estar na trilha"
        assert total[1] == 300, "e os tokens dos dois somados, não só 100"
    finally:
        await _limpar(user_id)


async def test_drenar_duas_vezes_nao_duplica(db) -> None:
    """A outra metade: idempotência.

    Sabotagem: remover o `ON CONFLICT (turn_id) DO NOTHING`. O segundo
    dreno insere de novo, e tokens e custo dobram.

    Esta metade e a de cima são inseparáveis: um teste só de "não
    duplica" passaria com um código que nunca insere; um só de "insere"
    passaria com o código antigo.
    """
    from sqlalchemy import text

    from app.services.turn_sync import drain_turn_outputs

    user_id = await _usuario()
    try:
        cid = await _conversa(db, user_id)
        await _turno_com_uso(
            db, user_id, cid, quando=datetime.now(UTC), custo=0.000354
        )

        async with db(user_id) as conn:
            await drain_turn_outputs(conn, user_id, cid)
            await drain_turn_outputs(conn, user_id, cid)
            await drain_turn_outputs(conn, user_id, cid)
            n = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM core.usage_log "
                        "WHERE user_id = :u"
                    ),
                    {"u": str(user_id)},
                )
            ).scalar_one()

        assert n == 1
    finally:
        await _limpar(user_id)


async def test_uso_e_copiado_mesmo_sem_mensagem_nova(db) -> None:
    """O INSERT saiu de trás do early-return.

    Sabotagem: devolver a chamada de `_copiar_uso` para depois do
    `if not copied: return 0`. O segundo dreno acha a mensagem já
    consumida, sai cedo, e o uso que faltava nunca é copiado.

    Reproduz o estado real: a saída foi drenada numa chamada anterior
    (`consumed = true`) e a trilha de uso está vazia.
    """
    from sqlalchemy import text

    from app.services.turn_sync import drain_turn_outputs

    user_id = await _usuario()
    try:
        cid = await _conversa(db, user_id)
        await _turno_com_uso(
            db, user_id, cid, quando=datetime.now(UTC), custo=0.000354
        )

        async with db(user_id) as conn:
            # Marca como consumido e esvazia a trilha: o estado de um
            # turno cuja mensagem foi drenada e cujo uso se perdeu.
            await conn.execute(
                text(
                    "UPDATE ai.turn_outputs SET consumed = true "
                    "WHERE user_id = :u"
                ),
                {"u": str(user_id)},
            )
            await conn.execute(
                text("DELETE FROM core.usage_log WHERE user_id = :u"),
                {"u": str(user_id)},
            )

            copiadas = await drain_turn_outputs(conn, user_id, cid)
            n = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM core.usage_log "
                        "WHERE user_id = :u"
                    ),
                    {"u": str(user_id)},
                )
            ).scalar_one()

        assert copiadas == 0, "nenhuma mensagem nova, mesmo"
        assert n == 1, "mas o uso tem de estar na trilha"
    finally:
        await _limpar(user_id)


async def test_uso_sem_turno_nao_entra_na_trilha_do_core(db) -> None:
    """O gasto da geração de título fica só no schema `ai`.

    `core.usage_log` depende de `turn_id` para a unique funcionar, e uso
    sem turno não tem chave. Ele é contabilizado onde é medido
    (`ai.turn_usage`, que é o que o painel lê) e não é arrastado para
    uma tabela cuja idempotência não o sustenta.

    Sabotagem: remover o `AND u.turn_id IS NOT NULL` do SELECT. Sem o
    filtro, cada dreno insere OUTRA linha para o mesmo gasto, porque
    `ON CONFLICT` não desduplica `NULL` — e a trilha cresce sem limite.
    """
    from sqlalchemy import text

    from app.services.turn_sync import drain_turn_outputs

    user_id = await _usuario()
    try:
        cid = await _conversa(db, user_id)

        # Pela engine do dono: escrever em `ai.*` é do worker (§2.3.2).
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
                        "provider_key, model, prompt_tokens, "
                        "completion_tokens, estimated_cost_usd, "
                        "used_instance_key) VALUES (NULL, :u, 'deepseek', "
                        "'deepseek-flash', 120, 8, 0.000021, false)"
                    ),
                    {"u": str(user_id)},
                )
        finally:
            await engine.dispose()

        async with db(user_id) as conn:
            await drain_turn_outputs(conn, user_id, cid)
            await drain_turn_outputs(conn, user_id, cid)

            n = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM core.usage_log "
                        "WHERE user_id = :u"
                    ),
                    {"u": str(user_id)},
                )
            ).scalar_one()
            no_ai = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM ai.turn_usage "
                        "WHERE user_id = :u AND turn_id IS NULL"
                    ),
                    {"u": str(user_id)},
                )
            ).scalar_one()

        assert n == 0, "não entra na trilha do core"
        assert no_ai == 1, "mas continua medido onde o painel lê"
    finally:
        await _limpar(user_id)
