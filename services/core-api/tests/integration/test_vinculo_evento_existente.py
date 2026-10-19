"""Ligar tarefa a bloco que JÁ ESTÁ na agenda (§5.7.1).

Este arquivo existe por um defeito medido na conta de verdade, e o
primeiro teste é a reprodução dele.

O usuário pediu, com estas palavras: "Tu tem como criar alguns tudulos,
por favor, vinculados a esses horários". O Age respondeu "Montei 8
itens, todos ligados ao bloco em que cada um cabe" e, mais adiante,
"como estão vinculadas ao bloco, concluir o item faz o Age lembrar que
existe um horário reservado".

O que estava no banco depois disso:

    plan_items das 8 tarefas  ->  links = []  nas oito
    core.task_event_links     ->  zero linhas para aquele plano

Duas causas independentes, e esta suíte cobre a segunda:

1. O modelo não tinha como dizer. `cobre_eventos` só aceita item
   empilhado NO MESMO TURNO, e os blocos tinham sido aprovados no turno
   anterior. (Coberto nos testes do worker.)
2. **Mesmo que dissesse, o executor descartava.**
   `_persist_task_event_links` resolvia cada id com `plan.item(...)`,
   que devolve `None` para quem não é item deste plano, e o vínculo
   morria num `continue`.

Por isso o cenário de `test_tarefa_nova_liga_em_bloco_de_outro_plano` é
deliberadamente o do usuário: o bloco é aplicado por um plano, e a
tarefa nasce em OUTRO, depois. Com o código anterior ele falha.

Precisa de Postgres: RLS, promoção e a gravação do vínculo são o que
está sendo testado, e nenhum dos três existe sem banco.
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
    # A engine é global e cacheada, e cada teste ganha um event loop
    # novo: sem o dispose, o segundo teste usa um pool preso ao loop
    # que já morreu.
    from app.core.db import dispose_engine, user_session

    await dispose_engine()
    yield user_session
    await dispose_engine()


def _engine_dono():
    """Engine com a role de migração (ver `test_promocao_rascunho.py`)."""
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
                {"u": str(user_id), "e": f"vinc-{user_id}@age.dev.br"},
            )
    finally:
        await engine.dispose()
    return user_id


async def _limpar(user_id: uuid.UUID) -> None:
    """Remove a conta e tudo que ela gerou.

    Roda contra o MESMO banco que o usuário usa de verdade, então deixar
    lixo aqui sujaria a conta dele. Filhos antes do pai: `plan_drafts`
    tem FK para `ai.turns`, e `task_event_links` para `core.users`.
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
                "core.task_event_links",
                "core.plan_items",
                "core.plans",
                "ai.plan_drafts",
                "ai.turns",
                "core.conversations",
                "core.local_events",
                "core.local_tasks",
                "core.local_calendars",
                "core.local_task_lists",
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


async def _calendario(factory, user_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy import text

    cal_id = uuid.uuid4()
    async with factory(user_id) as s:
        await s.execute(
            text(
                "INSERT INTO core.local_calendars (id, user_id, name, "
                "is_default) VALUES (:c, :u, 'Agenda', true)"
            ),
            {"c": str(cal_id), "u": str(user_id)},
        )
    return cal_id


async def _lista(factory, user_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy import text

    list_id = uuid.uuid4()
    async with factory(user_id) as s:
        await s.execute(
            text(
                "INSERT INTO core.local_task_lists (id, user_id, name, "
                "is_default) VALUES (:l, :u, 'Tarefas', true)"
            ),
            {"l": str(list_id), "u": str(user_id)},
        )
    return list_id


async def _bloco(
    factory,
    user_id: uuid.UUID,
    cal_id: uuid.UUID,
    *,
    titulo: str = "Planilha financeira: estrutura",
    inicio: datetime | None = None,
    duracao_min: int = 60,
) -> uuid.UUID:
    """Um bloco JÁ APLICADO na agenda, como o do turno anterior."""
    from sqlalchemy import text

    ev_id = uuid.uuid4()
    comeco = inicio or (datetime.now(UTC) + timedelta(hours=3))
    async with factory(user_id) as s:
        await s.execute(
            text(
                """
                INSERT INTO core.local_events
                  (id, user_id, local_calendar_id, title, start_at, end_at,
                   created_by_age)
                VALUES (:i, :u, :c, :t, :s, :e, true)
                """
            ),
            {
                "i": str(ev_id),
                "u": str(user_id),
                "c": str(cal_id),
                "t": titulo,
                "s": comeco,
                "e": comeco + timedelta(minutes=duracao_min),
            },
        )
    return ev_id


async def _tarefa(
    factory, user_id: uuid.UUID, list_id: uuid.UUID, titulo: str
) -> uuid.UUID:
    from sqlalchemy import text

    task_id = uuid.uuid4()
    async with factory(user_id) as s:
        await s.execute(
            text(
                """
                INSERT INTO core.local_tasks
                  (id, user_id, local_task_list_id, title, status)
                VALUES (:i, :u, :l, :t, 'open')
                """
            ),
            {
                "i": str(task_id),
                "u": str(user_id),
                "l": str(list_id),
                "t": titulo,
            },
        )
    return task_id


async def _conversa(factory, user_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy import text

    conv_id = uuid.uuid4()
    async with factory(user_id) as s:
        await s.execute(
            text(
                "INSERT INTO core.conversations (id, user_id, title_is_auto) "
                "VALUES (:c, :u, true)"
            ),
            {"c": str(conv_id), "u": str(user_id)},
        )
    return conv_id


async def _rascunho(
    factory, user_id: uuid.UUID, conv_id: uuid.UUID, itens: list[dict]
) -> uuid.UUID:
    """Grava um rascunho na forma exata de `StagingBuilder.to_json`."""
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
                    INSERT INTO ai.plan_drafts
                      (turn_id, user_id, conversation_id, summary, items)
                    VALUES (:t, :u, :c, 'checklist', CAST(:i AS json))
                    """
                ),
                {
                    "t": str(turn_id),
                    "u": str(user_id),
                    "c": str(conv_id),
                    "i": json.dumps(itens),
                },
            )
    finally:
        await engine.dispose()
    return turn_id


def _item_tarefa(titulo: str, refs: list[str], **extra) -> dict:
    """Um item de tarefa como o worker empilha, com `event_refs`."""
    return {
        "id": str(uuid.uuid4()),
        "target": "task",
        "op": "CREATE",
        "reason": "cobre o bloco combinado",
        "payload": {
            "title": titulo,
            "estimated_min": 15,
            "labels": ["Planilha financeira"],
            "event_refs": refs,
            **extra,
        },
        "links": [],
        "event_refs": refs,
        "sequence": 0,
    }


async def _aplicar(factory, user_id: uuid.UUID, turn_id: uuid.UUID):
    """Promove o rascunho e aplica o plano, como as rotas fazem."""
    from app.services.plan_store import promote_drafts

    async with factory(user_id) as s:
        promovidos = await promote_drafts(s, user_id)
    assert promovidos, "o rascunho não virou plano"
    plan_id = promovidos[-1]

    from app.adapters.calendar.local import LocalCalendar
    from app.adapters.tasks.local import LocalTasks
    from app.domain.plan_executor import apply_plan
    from app.services.plan_store import load_plan

    async with factory(user_id) as s:
        plano = await load_plan(s, user_id, plan_id)
        assert plano is not None
        resultado = await apply_plan(
            plano,
            LocalCalendar(db=s, user_id=user_id),
            tasks=LocalTasks(db=s, user_id=user_id),
            item_ids=[i.id for i in plano.items],
            confirm_blocking=True,
        )
        # A gravação do vínculo é feita pela rota, depois do executor.
        from app.api.plans import _persist_task_event_links

        pares = await _persist_task_event_links(
            s, user_id, plano, resultado
        )
    return plano, resultado, pares


async def _vinculos(factory, user_id: uuid.UUID) -> list[tuple[str, str]]:
    from sqlalchemy import text

    async with factory(user_id) as s:
        rows = await s.execute(
            text(
                "SELECT task_ref_id, event_ref_id FROM core.task_event_links "
                "WHERE user_id = :u AND deleted_at IS NULL"
            ),
            {"u": str(user_id)},
        )
    return [(str(r[0]), str(r[1])) for r in rows.all()]


class TestOCasoMedido:
    async def test_tarefa_nova_liga_em_bloco_de_outro_plano(self, db):
        """A reprodução do defeito do usuário.

        O bloco é aplicado ANTES, por outro plano. A tarefa nasce depois,
        em rascunho separado, apontando para o id real do bloco — que é
        exatamente o que o usuário pediu e o que não funcionava.

        Sabotagem que derruba: o `_persist_task_event_links` anterior,
        que só olhava `item.links` e portanto gravava zero vínculos.
        """
        user_id = await _usuario()
        try:
            cal = await _calendario(db, user_id)
            await _lista(db, user_id)
            bloco = await _bloco(db, user_id, cal)
            conv = await _conversa(db, user_id)

            turn = await _rascunho(
                db,
                user_id,
                conv,
                [_item_tarefa("Levantar gastos fixos", [str(bloco)])],
            )
            plano, resultado, pares = await _aplicar(db, user_id, turn)

            assert len(plano.items) == 1
            assert plano.items[0].event_refs == (
                ("local_events", str(bloco)),
            ), "o vínculo não sobreviveu à promoção"
            assert pares == 1
            assert len(resultado.applied) == 1

            vinculos = await _vinculos(db, user_id)
            assert len(vinculos) == 1
            assert vinculos[0][1] == str(bloco)
        finally:
            await _limpar(user_id)

    async def test_a_lista_passa_a_mostrar_o_bloco_reservado(self, db):
        """O que o usuário vê: a tarefa com horário, agrupável por hora.

        É o fim da linha que importa. `read_tasks` lê o vínculo por
        `_read_links`, sem saber quem o escreveu, e é dele que vem o
        `reserved` que a tela agrupa.
        """
        user_id = await _usuario()
        try:
            cal = await _calendario(db, user_id)
            await _lista(db, user_id)
            bloco = await _bloco(db, user_id, cal, titulo="Bloco de hoje")
            conv = await _conversa(db, user_id)

            turn = await _rascunho(
                db,
                user_id,
                conv,
                [_item_tarefa("Montar as abas", [str(bloco)])],
            )
            await _aplicar(db, user_id, turn)

            from app.services.task_store import read_tasks

            async with db(user_id) as s:
                entradas = await read_tasks(s, user_id)

            alvo = [e for e in entradas if e.task.title == "Montar as abas"]
            assert len(alvo) == 1
            reservados = alvo[0].reserved
            assert len(reservados) == 1, "a tarefa ficou sem horário na tela"
            assert reservados[0].title == "Bloco de hoje"
            assert alvo[0].reserved_minutes == 60
        finally:
            await _limpar(user_id)

    async def test_oito_tarefas_em_dois_blocos(self, db):
        """O caso de finanças, no tamanho real: 8 itens, 2 blocos.

        Sabotagem que derruba: laço que liga só o primeiro, ou que
        atribui todas ao mesmo bloco.
        """
        user_id = await _usuario()
        try:
            cal = await _calendario(db, user_id)
            await _lista(db, user_id)
            agora = datetime.now(UTC)
            hoje = await _bloco(
                db, user_id, cal, titulo="hoje", inicio=agora + timedelta(hours=2)
            )
            amanha = await _bloco(
                db,
                user_id,
                cal,
                titulo="amanhã",
                inicio=agora + timedelta(days=1, hours=2),
            )
            conv = await _conversa(db, user_id)

            itens = []
            for n in range(8):
                alvo = hoje if n < 4 else amanha
                item = _item_tarefa(f"item {n}", [str(alvo)])
                item["sequence"] = n
                itens.append(item)

            turn = await _rascunho(db, user_id, conv, itens)
            _plano, _res, pares = await _aplicar(db, user_id, turn)

            assert pares == 8
            vinculos = await _vinculos(db, user_id)
            assert len(vinculos) == 8
            por_bloco: dict[str, int] = {}
            for _tarefa_ref, ev in vinculos:
                por_bloco[ev] = por_bloco.get(ev, 0) + 1
            assert por_bloco == {str(hoje): 4, str(amanha): 4}
        finally:
            await _limpar(user_id)


class TestQuandoNaoDaParaLigar:
    async def test_bloco_apagado_entre_propor_e_aplicar(self, db):
        """A tarefa continua valendo; só o vínculo se perde.

        Perder o vínculo é degradação visível (a tarefa aparece sem
        horário). Derrubar a tarefa junto seria pior, e não é o que o
        usuário pediu.

        Sabotagem que derruba: confiar no id empilhado sem reconferir na
        promoção, o que gravaria vínculo para evento inexistente.
        """
        from sqlalchemy import text

        user_id = await _usuario()
        try:
            cal = await _calendario(db, user_id)
            await _lista(db, user_id)
            bloco = await _bloco(db, user_id, cal)
            conv = await _conversa(db, user_id)
            turn = await _rascunho(
                db, user_id, conv, [_item_tarefa("Sobrevive", [str(bloco)])]
            )

            # O usuário apaga o bloco DEPOIS de o Age propor.
            async with db(user_id) as s:
                await s.execute(
                    text(
                        "UPDATE core.local_events SET deleted_at = now() "
                        "WHERE id = :i AND user_id = :u"
                    ),
                    {"i": str(bloco), "u": str(user_id)},
                )

            plano, resultado, pares = await _aplicar(db, user_id, turn)

            assert len(plano.items) == 1, "a tarefa foi descartada junto"
            assert plano.items[0].event_refs == ()
            assert pares == 0
            assert len(resultado.applied) == 1
            assert await _vinculos(db, user_id) == []
        finally:
            await _limpar(user_id)

    async def test_bloco_que_ja_terminou_nao_vira_tempo_reservado(self, db):
        """Bloco encerrado é recusado na promoção.

        Ligar a ele faria `reserved_minutes` contar tempo que já passou,
        e a lista diria "1h reservada" para uma hora que não existe.

        Sabotagem que derruba: tirar a checagem de `end <= agora`.
        """
        user_id = await _usuario()
        try:
            cal = await _calendario(db, user_id)
            await _lista(db, user_id)
            passado = await _bloco(
                db,
                user_id,
                cal,
                titulo="já terminou",
                inicio=datetime.now(UTC) - timedelta(hours=5),
            )
            conv = await _conversa(db, user_id)
            turn = await _rascunho(
                db, user_id, conv, [_item_tarefa("Tarde demais", [str(passado)])]
            )

            plano, _res, pares = await _aplicar(db, user_id, turn)

            assert plano.items[0].event_refs == ()
            assert pares == 0
            assert await _vinculos(db, user_id) == []
        finally:
            await _limpar(user_id)

    async def test_bloco_de_outro_usuario_nao_resolve(self, db):
        """RLS, e não um `WHERE` que alguém pode esquecer.

        Sabotagem que derruba: consultar sem `user_session`, ou tirar o
        filtro de usuário do `resolve_events`.
        """
        dono = await _usuario()
        intruso = await _usuario()
        try:
            cal = await _calendario(db, dono)
            bloco = await _bloco(db, dono, cal, titulo="bloco do dono")

            from app.services.task_store import resolve_events

            async with db(intruso) as s:
                achados = await resolve_events(s, intruso, [str(bloco)])

            assert len(achados) == 1
            assert achados[0].found is False
            # Nem o título vaza.
            assert achados[0].title is None
        finally:
            await _limpar(intruso)
            await _limpar(dono)


class TestIdempotenciaEDesfazer:
    async def test_ligar_duas_vezes_nao_duplica(self, db):
        """O mesmo par em dois planos gera uma linha só.

        Sabotagem que derruba: tirar o `ON CONFLICT DO NOTHING` de
        `create_link`.
        """
        user_id = await _usuario()
        try:
            cal = await _calendario(db, user_id)
            await _lista(db, user_id)
            bloco = await _bloco(db, user_id, cal)
            tarefa = await _tarefa(
                db, user_id, (await _listas(db, user_id))[0], "Repetida"
            )
            conv = await _conversa(db, user_id)

            for _ in range(2):
                item = {
                    "id": str(uuid.uuid4()),
                    "target": "task",
                    "op": "LINK",
                    "reason": "liga de novo",
                    "payload": {
                        "task_id": str(tarefa),
                        "event_refs": [str(bloco)],
                    },
                    "links": [],
                    "event_refs": [str(bloco)],
                    "sequence": 0,
                }
                turn = await _rascunho(db, user_id, conv, [item])
                await _aplicar(db, user_id, turn)

            vinculos = await _vinculos(db, user_id)
            assert len(vinculos) == 1
        finally:
            await _limpar(user_id)

    async def test_desfazer_o_plano_remove_o_vinculo(self, db):
        """O desfazer acha estes vínculos pelo `plan_id`.

        Sabotagem que derruba: esquecer `plan_id=plan.id` na chamada
        nova de `create_link` — o vínculo ficaria para trás e a tarefa
        continuaria dizendo "tempo reservado" depois do desfazer.
        """
        user_id = await _usuario()
        try:
            cal = await _calendario(db, user_id)
            await _lista(db, user_id)
            bloco = await _bloco(db, user_id, cal)
            conv = await _conversa(db, user_id)
            turn = await _rascunho(
                db, user_id, conv, [_item_tarefa("Desfeita", [str(bloco)])]
            )
            plano, _res, pares = await _aplicar(db, user_id, turn)
            assert pares == 1

            from app.services.task_store import remove_links_for_plan

            async with db(user_id) as s:
                await remove_links_for_plan(s, user_id, plano.id)

            assert await _vinculos(db, user_id) == []
        finally:
            await _limpar(user_id)


async def _listas(factory, user_id: uuid.UUID) -> list[uuid.UUID]:
    from sqlalchemy import text

    async with factory(user_id) as s:
        rows = await s.execute(
            text(
                "SELECT id FROM core.local_task_lists WHERE user_id = :u "
                "ORDER BY created_at"
            ),
            {"u": str(user_id)},
        )
    return [r[0] for r in rows.all()]
