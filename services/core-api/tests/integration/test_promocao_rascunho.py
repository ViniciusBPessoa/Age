"""Promover rascunho de plano sem depender do navegador (§2.3.2).

Relato: "quando ele pega uma mensagem de consentimento para fazer a
marcação, não estava funcionando adequadamente". Era o cartão de
aprovação não aparecendo — de novo, e por uma causa diferente da que
eu tinha corrigido antes.

O que estava medido na conta real quando fui olhar:

    20:24:12.77   a tela busca GET /plans    <- antes do rascunho
    20:24:15      o worker grava o rascunho
    (nunca mais)  nada busca de novo

`promote_drafts` só rodava dentro do `GET /plans`, e a tela só chamava
`/plans` ao receber o evento `plan`. Se essa única busca chegasse
antes do rascunho ser gravado — 2,2 s nesse caso — o rascunho ficava
`promoted = false` e a proposta ficava **invisível para sempre**.
Encontrei três rascunhos presos assim, um deles do usuário.

A correção não é esperar mais: é tirar o relógio do navegador da
equação. `GET /conversations/{id}/messages` é chamada no `done` de
todo turno (a tela recarrega as mensagens do banco), então promover
ali garante que, quando a resposta aparece, o plano já existe.

Estes testes precisam de banco (RLS, a função de promoção), então
rodam como integração.
"""

from __future__ import annotations

import json
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
    # Mesmo motivo de `test_plan_items.py`: a engine é global e cacheada,
    # e cada teste ganha um event loop novo.
    from app.core.db import dispose_engine, user_session

    await dispose_engine()
    yield user_session
    await dispose_engine()


def _engine_dono():
    """Engine com a role de migração.

    Criar e remover CONTA está fora do que a role de runtime pode
    fazer (RLS, §4.2.1). Minha primeira versão tentou
    `user_session(None)` e o `None` chegou ao Postgres como a string
    "None" num campo uuid — erro de preparo disfarçado de erro de
    produto.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.getenv("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
    return create_async_engine(url)


async def _usuario(_session_factory) -> uuid.UUID:
    """Uma conta nova, criada pela role de migração."""
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
                {"u": str(user_id), "e": f"promo-{user_id}@age.dev.br"},
            )
    finally:
        await engine.dispose()
    return user_id


async def _limpar(user_id: uuid.UUID) -> None:
    """Remove a conta e o que ela gerou.

    Estes testes rodam contra o Postgres do compose, que é o MESMO
    banco que o usuário usa de verdade. Deixar conta de teste para trás
    sujaria a conta dele com planos e rascunhos que ele não pediu.
    """
    from sqlalchemy import text

    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            # `app.user_id` precisa existir nesta transação: as
            # políticas de RLS de `ai.*` leem o GUC, e sem o
            # `set_config` o DELETE estoura com "unrecognized
            # configuration parameter".
            await conn.execute(
                text("SELECT set_config('app.user_id', :u, true)"),
                {"u": str(user_id)},
            )
            # A ORDEM importa, e `ai.turns` faltava na minha primeira
            # versão: `plan_drafts.turn_id` tem FK para `ai.turns`, e
            # apagar só o usuário deixou 17 linhas órfãs no banco que o
            # usuário usa de verdade. Filhos primeiro, pai depois.
            for tabela in (
                "core.plan_items",
                "core.plans",
                "ai.plan_drafts",
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

    conv_id = uuid.uuid4()
    async with session_factory(user_id) as s:
        await s.execute(
            text(
                "INSERT INTO core.conversations (id, user_id, title_is_auto) "
                "VALUES (:c, :u, true)"
            ),
            {"c": str(conv_id), "u": str(user_id)},
        )
    return conv_id


async def _rascunho(
    session_factory, user_id: uuid.UUID, conv_id: uuid.UUID
) -> uuid.UUID:
    """Um rascunho como o worker grava: um evento proposto."""
    from sqlalchemy import text

    turn_id = uuid.uuid4()
    inicio = datetime.now(UTC) + timedelta(days=2)
    # A forma é a do worker (`StagingBuilder.to_json`), não uma
    # invenção minha: o campo é `payload`, e `_promote_one` lê
    # `raw.get("payload")`. Minha primeira versão escreveu `after` — o
    # nome da coluna de `plan_items` — e o item foi descartado em
    # silêncio por ficar sem horário. O plano nascia `proposed` com
    # ZERO itens, que é um cartão vazio.
    itens = json.dumps(
        [
            {
                "id": str(uuid.uuid4()),
                "target": "event",
                "op": "CREATE",
                "reason": "sessão de estudo",
                "payload": {
                    "title": "Classes em Python",
                    "start": inicio.isoformat(),
                    "end": (inicio + timedelta(hours=1)).isoformat(),
                    "all_day": False,
                    "source": "age",
                },
                "links": [],
                "sequence": 0,
            }
        ]
    )
    # Pela role de migração: `ai.plan_drafts` é ESCRITA pelo worker, e a
    # role de runtime do core-api só tem leitura ali. O teste imita o
    # worker, então precisa do mesmo alcance que ele — tentar pela
    # sessão normal dá "permission denied for table plan_drafts".
    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            # A RLS vale também para a role de migração nesta tabela, e
            # `set_config(..., true)` tem escopo de TRANSAÇÃO — por isso
            # vai dentro do mesmo `begin()` do INSERT.
            await conn.execute(
                text("SELECT set_config('app.user_id', :u, true)"),
                {"u": str(user_id)},
            )
            # O turno precisa existir: `plan_drafts.turn_id` tem FK para
            # `ai.turns`. O worker cria o turno antes do rascunho, e o
            # teste segue a mesma ordem.
            await conn.execute(
                text(
                    "INSERT INTO ai.turns (id, user_id, conversation_id, "
                    "status) VALUES (:t, :u, :c, 'done')"
                ),
                {"t": str(turn_id), "u": str(user_id), "c": str(conv_id)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ai.plan_drafts
                      (turn_id, user_id, conversation_id, summary, items)
                    VALUES (:t, :u, :c, '1 sessão', CAST(:i AS json))
                    """
                ),
                {
                    "t": str(turn_id),
                    "u": str(user_id),
                    "c": str(conv_id),
                    "i": itens,
                },
            )
    finally:
        await engine.dispose()
    return turn_id


async def _promovido(session_factory, user_id: uuid.UUID, turn_id: uuid.UUID):
    from sqlalchemy import text

    async with session_factory(user_id) as s:
        row = (
            await s.execute(
                text(
                    "SELECT promoted, promoted_plan_id FROM ai.plan_drafts "
                    "WHERE turn_id = :t AND user_id = :u"
                ),
                {"t": str(turn_id), "u": str(user_id)},
            )
        ).first()
    return row




async def test_promocao_nao_depende_do_navegador(db):
    """O coração da correção.

    Antes, só `GET /plans` promovia, e a tela só chamava `/plans` ao
    receber o evento `plan`. Uma única busca, no instante errado, e a
    proposta ficava invisível para sempre.
    """
    from app.services.plan_store import promote_drafts

    user_id = await _usuario(db)
    try:
        conv_id = await _conversa(db, user_id)
        turn_id = await _rascunho(db, user_id, conv_id)

        antes = await _promovido(db, user_id, turn_id)
        assert antes.promoted is False, "o rascunho nasce não promovido"

        async with db(user_id) as s:
            criados = await promote_drafts(s, user_id)

        assert len(criados) == 1
        depois = await _promovido(db, user_id, turn_id)
        assert depois.promoted is True
        assert depois.promoted_plan_id is not None
    finally:
        await _limpar(user_id)


async def test_o_plano_promovido_e_aprovavel(db):
    """Promover tem de produzir algo APROVÁVEL.

    Um plano sem item pendente renderia um cartão vazio, tão inútil
    quanto cartão nenhum — e passaria num teste que só checasse
    `promoted = true`.
    """
    from sqlalchemy import text

    from app.services.plan_store import promote_drafts

    user_id = await _usuario(db)
    try:
        conv_id = await _conversa(db, user_id)
        turn_id = await _rascunho(db, user_id, conv_id)

        async with db(user_id) as s:
            await promote_drafts(s, user_id)

        linha = await _promovido(db, user_id, turn_id)
        async with db(user_id) as s:
            plano = (
                await s.execute(
                    text(
                        "SELECT status, conversation_id FROM core.plans "
                        "WHERE id = :p AND user_id = :u"
                    ),
                    {"p": str(linha.promoted_plan_id), "u": str(user_id)},
                )
            ).first()
            itens = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM core.plan_items "
                        "WHERE plan_id = :p AND status = 'pending'"
                    ),
                    {"p": str(linha.promoted_plan_id)},
                )
            ).scalar()

        assert plano.status == "proposed"
        # A conversa precisa vir junto: a tela filtra as propostas pela
        # conversa aberta, e um plano sem ela nunca apareceria.
        assert str(plano.conversation_id) == str(conv_id)
        assert itens == 1
    finally:
        await _limpar(user_id)


async def test_promover_duas_vezes_nao_duplica(db):
    """Idempotência, e ela deixou de ser detalhe.

    Agora DUAS rotas promovem: `GET /plans` e `GET .../messages`. As
    duas são chamadas quase juntas no fim de um turno, então uma
    promoção não idempotente daria dois planos para o mesmo rascunho —
    dois cartões para a mesma proposta. É o mesmo sintoma de
    duplicação que o usuário já relatou por outra causa.
    """
    from app.services.plan_store import promote_drafts

    user_id = await _usuario(db)
    try:
        conv_id = await _conversa(db, user_id)
        await _rascunho(db, user_id, conv_id)

        async with db(user_id) as s:
            primeira = await promote_drafts(s, user_id)
        async with db(user_id) as s:
            segunda = await promote_drafts(s, user_id)

        assert len(primeira) == 1
        assert segunda == [], "rascunho promovido não pode virar outro plano"
    finally:
        await _limpar(user_id)


async def test_rascunho_de_outro_usuario_nao_e_promovido(db):
    """A promoção varre por `user_id`.

    Limitação registrada: o RLS já bloquearia isso, então este teste
    passa mesmo se o filtro da query for removido. Guarda a intenção,
    não a única defesa.
    """
    from app.services.plan_store import promote_drafts

    dono = await _usuario(db)
    outro = await _usuario(db)
    try:
        conv_dono = await _conversa(db, dono)
        turn_id = await _rascunho(db, dono, conv_dono)

        async with db(outro) as s:
            criados = await promote_drafts(s, outro)

        assert criados == []
        linha = await _promovido(db, dono, turn_id)
        assert linha.promoted is False, "o rascunho do dono fica intacto"
    finally:
        await _limpar(dono)
        await _limpar(outro)


async def test_duas_promocoes_simultaneas_nao_duplicam(db):
    """A corrida real, e o teste que faltava.

    Meu `test_promover_duas_vezes_nao_duplica` roda as promoções EM
    SEQUÊNCIA, e em sequência o código errado acerta: a segunda vê
    `promoted = true` e para. Por isso ele passou enquanto o usuário
    recebia propostas duplicadas.

    Medido na conta dele:

        23:57:47.938   GET /plans
        23:57:47.944   GET /conversations/{id}/messages

    Seis milissegundos. As duas transações leram o mesmo rascunho com
    `promoted = false`, as duas promoveram, e sobraram dois planos
    idênticos de 7 itens — 12 cartões onde deviam haver 6.

    `asyncio.gather` com duas sessões DISTINTAS é o que reproduz isso:
    duas transações de verdade, concorrendo no banco. Uma sessão só,
    reusada, seria uma transação só e não testaria nada.
    """
    import asyncio

    from sqlalchemy import text

    from app.services.plan_store import promote_drafts

    user_id = await _usuario(db)
    try:
        conv_id = await _conversa(db, user_id)
        await _rascunho(db, user_id, conv_id)

        async def promover() -> list:
            async with db(user_id) as s:
                return await promote_drafts(s, user_id)

        resultados = await asyncio.gather(
            promover(), promover(), return_exceptions=True
        )

        # Nenhuma das duas pode estourar: a perdedora pula o rascunho,
        # ela não falha.
        for r in resultados:
            assert not isinstance(r, BaseException), f"promoção estourou: {r}"

        # O que importa não é qual venceu, é que o banco tenha UM plano.
        async with db(user_id) as s:
            planos = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM core.plans "
                        "WHERE user_id = :u AND status = 'proposed'"
                    ),
                    {"u": str(user_id)},
                )
            ).scalar()

        assert planos == 1, (
            f"{planos} planos para um rascunho: é a duplicação que o "
            "usuário viu na tela"
        )
    finally:
        await _limpar(user_id)


async def _saida_de_turno(user_id: uuid.UUID, conv_id: uuid.UUID) -> uuid.UUID:
    """Uma resposta do worker esperando ser copiada para `core.messages`."""
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
                    "status) VALUES (:t, :u, :c, 'done')"
                ),
                {"t": str(turn_id), "u": str(user_id), "c": str(conv_id)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ai.turn_outputs
                      (turn_id, user_id, conversation_id, role, content,
                       sequence, consumed)
                    VALUES (:t, :u, :c, 'assistant', 'Preparei 7 blocos',
                            0, false)
                    """
                ),
                {"t": str(turn_id), "u": str(user_id), "c": str(conv_id)},
            )
    finally:
        await engine.dispose()
    return turn_id


async def test_dois_drenos_simultaneos_nao_duplicam_mensagem(db):
    """A mesma corrida, na cópia das mensagens.

    O histórico do usuário veio com cada mensagem DUAS vezes, pergunta
    e resposta. A causa é a mesma do plano duplicado: o SELECT de
    `ai.turn_outputs` olhava `consumed = false` sem lock, e as duas
    leituras concorrentes do fim do turno copiaram a mesma linha.

    O docstring do módulo dizia "idempotente", e era verdade só em
    sequência. Este teste cobre o caso que importa.
    """
    import asyncio

    from sqlalchemy import text

    from app.services.turn_sync import drain_turn_outputs

    user_id = await _usuario(db)
    try:
        conv_id = await _conversa(db, user_id)
        await _saida_de_turno(user_id, conv_id)

        async def drenar() -> int:
            async with db(user_id) as s:
                return await drain_turn_outputs(s, user_id, conv_id)

        resultados = await asyncio.gather(
            drenar(), drenar(), return_exceptions=True
        )
        for r in resultados:
            assert not isinstance(r, BaseException), f"dreno estourou: {r}"

        async with db(user_id) as s:
            copias = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM core.messages "
                        "WHERE conversation_id = :c AND user_id = :u"
                    ),
                    {"c": str(conv_id), "u": str(user_id)},
                )
            ).scalar()

        assert copias == 1, (
            f"{copias} cópias da mesma resposta: é o histórico "
            "duplicado que o usuário viu"
        )
    finally:
        await _limpar(user_id)
