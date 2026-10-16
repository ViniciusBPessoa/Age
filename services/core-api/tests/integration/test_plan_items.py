"""Descartar e remarcar UM item proposto (§5.8).

Relato do usuário, literal: "está lá como proposta, não posso mais
alterar, não posso mais editar, que porra é essa".

Ele tinha razão. Havia `apply` (aprova tudo ou um subconjunto) e
`reject` (joga o plano inteiro fora), e nada entre os dois. Quem
recebeu 18 blocos de estudo e queria tirar 3 não tinha caminho: era
tudo ou nada, e só pelo cartão do turno que os criou — que some assim
que a conversa avança.

Estes testes precisam de banco (RLS, `jsonb_set`), então rodam como
integração. `psycopg`/asyncpg contra o Postgres do compose.
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
    """A engine do app é global e cacheada (`app.core.db._engine`), mas o
    `asyncio_mode = "auto"` dá um event loop NOVO a cada teste.

    O pool criado no teste anterior fica preso ao loop morto, e a
    segunda requisição de qualquer teste estoura com "attached to a
    different loop" — um erro que parece bug da rota e é do preparo.
    `dispose_engine` já existe para o shutdown da aplicação; aqui ela
    garante que cada teste comece com o pool fechado.
    """
    from app.core.db import dispose_engine, user_session

    await dispose_engine()
    yield user_session
    await dispose_engine()


async def _criar_plano(session_factory, user_id: uuid.UUID, quantos: int):
    """Um plano `proposed` com N itens de evento pendentes."""
    from sqlalchemy import text

    plan_id = uuid.uuid4()
    item_ids = [uuid.uuid4() for _ in range(quantos)]
    base = datetime.now(UTC) + timedelta(days=1)

    async with session_factory(user_id) as db:
        await db.execute(
            # `expires_at` é NOT NULL: plano proposto caduca (§5.8), e
            # a coluna existe justamente para isso.
            text(
                "INSERT INTO core.plans (id, user_id, status, created_at, "
                "expires_at) VALUES (:p, :u, 'proposed', now(), :x)"
            ),
            {
                "p": str(plan_id),
                "u": str(user_id),
                "x": datetime.now(UTC) + timedelta(days=1),
            },
        )
        for i, item_id in enumerate(item_ids):
            inicio = base + timedelta(hours=i)
            # `json.dumps`, não interpolação: um título com aspas
            # produziria JSON inválido, e o teste falharia por um
            # motivo que não é o que ele investiga.
            payload = json.dumps(
                {
                    "title": f"Bloco {i}",
                    "start": inicio.isoformat(),
                    "end": (inicio + timedelta(hours=1)).isoformat(),
                    "all_day": False,
                    "source": "age",
                }
            )
            await db.execute(
                text(
                    """
                    INSERT INTO core.plan_items
                      (id, user_id, plan_id, target, op, after, sequence,
                       status, reason)
                    VALUES (:i, :u, :p, 'event', 'CREATE', CAST(:a AS json), :s,
                            'pending', 'bloco de teste')
                    """
                ),
                {
                    "i": str(item_id),
                    "u": str(user_id),
                    "p": str(plan_id),
                    "a": payload,
                    "s": i,
                },
            )
    return plan_id, item_ids


async def _status(session_factory, user_id, *, plan_id=None, item_id=None):
    from sqlalchemy import text

    async with session_factory(user_id) as db:
        if item_id is not None:
            row = await db.execute(
                text("SELECT status FROM core.plan_items WHERE id = :i"),
                {"i": str(item_id)},
            )
        else:
            row = await db.execute(
                text("SELECT status FROM core.plans WHERE id = :p"),
                {"p": str(plan_id)},
            )
        return row.scalar_one_or_none()


def _cliente(user_id: uuid.UUID):
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import AuthenticatedUser, get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user_id, session_id=uuid.uuid4(), role="user"
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _engine_dono():
    """Engine com a role de migração: criar e remover CONTA está fora do
    que a role de runtime pode fazer (RLS, §4.2.1), e é operação de
    preparo de teste, não de produto."""
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.getenv("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
    return create_async_engine(url)


async def _criar_conta() -> uuid.UUID:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO core.users (id, email, password_hash, timezone) "
                    "VALUES (:i, :e, 'x', 'America/Sao_Paulo')"
                ),
                {"i": str(user_id), "e": f"t-{user_id}@age.dev.br"},
            )
    finally:
        await engine.dispose()
    return user_id


async def _remover_conta(user_id: uuid.UUID) -> None:
    from sqlalchemy import text

    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM core.plan_items WHERE user_id = :i"),
                {"i": str(user_id)},
            )
            await conn.execute(
                text("DELETE FROM core.plans WHERE user_id = :i"),
                {"i": str(user_id)},
            )
            await conn.execute(
                text("DELETE FROM core.users WHERE id = :i"), {"i": str(user_id)}
            )
    finally:
        await engine.dispose()


@pytest.fixture
async def usuario():
    """Conta descartável, criada e removida por teste."""
    user_id = await _criar_conta()
    yield user_id
    await _remover_conta(user_id)


class TestDescartarItem:
    async def test_descarta_um_sem_tocar_nos_outros(self, db, usuario):
        """O caso do relato: tirar 3 de 18 sem perder os 15."""
        plan_id, itens = await _criar_plano(db, usuario, 3)

        async with _cliente(usuario) as c:
            resposta = await c.delete(f"/api/v1/plans/items/{itens[0]}")

        assert resposta.status_code == 204
        assert await _status(db, usuario, item_id=itens[0]) == "rejected"
        assert await _status(db, usuario, item_id=itens[1]) == "pending"
        assert await _status(db, usuario, item_id=itens[2]) == "pending"
        # O plano continua de pé: ainda há o que aprovar nele.
        assert await _status(db, usuario, plan_id=plan_id) == "proposed"

    async def test_plano_fecha_quando_o_ultimo_item_sai(self, db, usuario):
        """Senão o plano fica `proposed` para sempre, contando no selo da
        agenda e pedindo uma aprovação que não tem mais o que aprovar."""
        plan_id, itens = await _criar_plano(db, usuario, 2)

        async with _cliente(usuario) as c:
            await c.delete(f"/api/v1/plans/items/{itens[0]}")
        # A verificação fica FORA do bloco do cliente: misturar a sessão
        # do banco com o ASGITransport aberto prende as duas pools a
        # loops diferentes e o erro ("attached to a different loop")
        # parece bug da rota, quando é do teste.
        assert await _status(db, usuario, plan_id=plan_id) == "proposed"

        async with _cliente(usuario) as c:
            await c.delete(f"/api/v1/plans/items/{itens[1]}")

        assert await _status(db, usuario, plan_id=plan_id) == "rejected"

    async def test_item_inexistente_da_404(self, db, usuario):
        async with _cliente(usuario) as c:
            resposta = await c.delete(f"/api/v1/plans/items/{uuid.uuid4()}")
        assert resposta.status_code == 404

    async def test_descartar_duas_vezes_nao_passa_silencioso(self, db, usuario):
        """Segunda chamada dá 404 porque o item já não está `pending`.

        Importa para a UI: um duplo-clique não deve parecer sucesso duas
        vezes, e um item já aplicado não pode ser "descartado" depois de
        virar evento na agenda.
        """
        _plan, itens = await _criar_plano(db, usuario, 2)

        async with _cliente(usuario) as c:
            primeira = await c.delete(f"/api/v1/plans/items/{itens[0]}")
            segunda = await c.delete(f"/api/v1/plans/items/{itens[0]}")

        assert primeira.status_code == 204
        assert segunda.status_code == 404


class TestRemarcarItem:
    async def test_muda_o_horario_do_item(self, db, usuario):
        """"Esse bloco serve, mas não às 8h15." Sem isto a única saída é
        rejeitar e gastar um turno do modelo para mover meia hora."""
        from sqlalchemy import text

        _plan, itens = await _criar_plano(db, usuario, 1)
        novo = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)

        async with _cliente(usuario) as c:
            resposta = await c.patch(
                f"/api/v1/plans/items/{itens[0]}",
                json={
                    "start": novo.isoformat(),
                    "end": (novo + timedelta(hours=2)).isoformat(),
                },
            )

        assert resposta.status_code == 204
        async with db(usuario) as conn:
            row = await conn.execute(
                text(
                    "SELECT CAST(after AS jsonb)->>'start', "
                    "CAST(after AS jsonb)->>'end' "
                    "FROM core.plan_items WHERE id = :i"
                ),
                {"i": str(itens[0])},
            )
            inicio, fim = row.one()

        assert datetime.fromisoformat(inicio) == novo
        assert datetime.fromisoformat(fim) == novo + timedelta(hours=2)

    async def test_preserva_o_resto_do_payload(self, db, usuario):
        """Só `start` e `end` mudam. Título e origem continuam: o que
        esta rota resolve é horário, e reescrever o payload inteiro
        transformaria aprovação em formulário."""
        from sqlalchemy import text

        _plan, itens = await _criar_plano(db, usuario, 1)
        novo = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)

        async with _cliente(usuario) as c:
            await c.patch(
                f"/api/v1/plans/items/{itens[0]}",
                json={
                    "start": novo.isoformat(),
                    "end": (novo + timedelta(hours=1)).isoformat(),
                },
            )

        async with db(usuario) as conn:
            row = await conn.execute(
                text(
                    "SELECT CAST(after AS jsonb)->>'title', "
                    "CAST(after AS jsonb)->>'source' "
                    "FROM core.plan_items WHERE id = :i"
                ),
                {"i": str(itens[0])},
            )
            titulo, origem = row.one()

        assert titulo == "Bloco 0"
        assert origem == "age"

    async def test_recusa_fim_antes_do_inicio(self, db, usuario):
        """Sem a guarda, o bloco entra com duração negativa e o
        Scheduler passa a ver um intervalo impossível."""
        _plan, itens = await _criar_plano(db, usuario, 1)
        inicio = datetime.now(UTC) + timedelta(days=4)

        async with _cliente(usuario) as c:
            resposta = await c.patch(
                f"/api/v1/plans/items/{itens[0]}",
                json={
                    "start": inicio.isoformat(),
                    "end": (inicio - timedelta(hours=1)).isoformat(),
                },
            )

        assert resposta.status_code == 400

    async def test_nao_remarca_item_ja_descartado(self, db, usuario):
        """Remarcar o que já foi decidido o traria de volta pela porta
        dos fundos, sem passar por aprovação."""
        _plan, itens = await _criar_plano(db, usuario, 2)
        novo = datetime.now(UTC) + timedelta(days=5)

        async with _cliente(usuario) as c:
            await c.delete(f"/api/v1/plans/items/{itens[0]}")
            resposta = await c.patch(
                f"/api/v1/plans/items/{itens[0]}",
                json={
                    "start": novo.isoformat(),
                    "end": (novo + timedelta(hours=1)).isoformat(),
                },
            )

        assert resposta.status_code == 404


class TestIsolamentoEntreContas:
    async def test_nao_descarta_proposta_de_outra_conta(self, db, usuario):
        """RLS (§4.2.3). O id do item é adivinhável por força bruta.

        Honestidade sobre o que este teste prova: ele verifica a
        GARANTIA, não o mecanismo. Removi o `AND user_id = :uid` da
        rota e ele continuou verde, porque a política do banco já
        barra sozinha. O filtro na query é defesa em profundidade e
        continua ali de propósito, mas quem o apagar não vai descobrir
        por aqui — vai descobrir no dia em que alguém rodar a rota com
        uma sessão sem `app.user_id`.
        """
        outro = await _criar_conta()
        try:
            _plan, itens = await _criar_plano(db, outro, 1)

            async with _cliente(usuario) as c:
                resposta = await c.delete(f"/api/v1/plans/items/{itens[0]}")

            assert resposta.status_code == 404
            assert await _status(db, outro, item_id=itens[0]) == "pending"
        finally:
            await _remover_conta(outro)
