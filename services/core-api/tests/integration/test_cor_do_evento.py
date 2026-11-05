"""Cor de categoria no evento (migration 0015), ponta a ponta.

Contra o Postgres de verdade: o que importa aqui é que a cor sobrevive à
ida e volta pelo adaptador (quatro pontos de leitura com a mesma lista
de colunas por índice) e que o banco recusa um nome fora da paleta
mesmo que a rota deixe passar.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="precisa do Postgres e do Redis (make up)",
)

JANELA = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-30T23:59:59Z"}


@pytest.fixture(autouse=True)
async def _recursos_limpos():
    from app.core.db import dispose_engine
    from app.core.redis import close_redis

    await dispose_engine()
    await close_redis()
    yield
    await dispose_engine()
    await close_redis()


def _cliente():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    app.dependency_overrides.clear()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _conta(c) -> dict[str, str]:
    n = uuid.uuid4().int
    r = await c.post(
        "/api/v1/auth/register",
        json={
            "email": f"cor-{uuid.uuid4().hex[:10]}@age.dev.br",
            "password": "SenhaForte#2026",
        },
        headers={"X-Forwarded-For": f"10.9.{n % 250}.{(n >> 8) % 250 + 1}"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _listar(c, auth) -> dict[str, dict]:
    r = await c.get("/api/v1/calendar/events", params=JANELA, headers=auth)
    return {e["id"]: e for e in r.json()["events"]}


async def test_cor_vai_e_volta_e_some_com_null() -> None:
    """Sabotagem: esquecer `color` em qualquer um dos SELECT/RETURNING."""
    async with _cliente() as c:
        auth = await _conta(c)
        r = await c.post(
            "/api/v1/calendar/events",
            json={
                "title": "Aula de Cálculo",
                "start": "2026-11-10T08:00:00-03:00",
                "end": "2026-11-10T10:00:00-03:00",
                "color": "mirtilo",
            },
            headers=auth,
        )
        assert r.status_code == 201, r.text
        ev = r.json()
        assert ev["color"] == "mirtilo"
        assert (await _listar(c, auth))[ev["id"]]["color"] == "mirtilo"

        # PATCH sem `color` mantém a cor.
        r = await c.patch(
            f"/api/v1/calendar/events/{ev['id']}",
            json={"title": "Aula de Cálculo II"},
            headers=auth,
        )
        assert r.json()["color"] == "mirtilo"

        # PATCH com outra cor troca.
        r = await c.patch(
            f"/api/v1/calendar/events/{ev['id']}",
            json={"color": "salvia"},
            headers=auth,
        )
        assert r.json()["color"] == "salvia"

        # `null` explícito tira a cor.
        r = await c.patch(
            f"/api/v1/calendar/events/{ev['id']}",
            json={"color": None},
            headers=auth,
        )
        assert r.json()["color"] is None
        assert (await _listar(c, auth))[ev["id"]]["color"] is None


async def test_cor_fora_da_paleta_e_recusada() -> None:
    """Sabotagem: trocar o `Literal` por `str`."""
    async with _cliente() as c:
        auth = await _conta(c)
        r = await c.post(
            "/api/v1/calendar/events",
            json={
                "title": "x",
                "start": "2026-11-10T08:00:00-03:00",
                "end": "2026-11-10T09:00:00-03:00",
                "color": "#ff0000",
            },
            headers=auth,
        )
        assert r.status_code == 422


async def test_banco_recusa_cor_fora_da_paleta() -> None:
    """A segunda barreira: o CHECK da 0015, sem passar pela rota.

    Sabotagem: apagar o `ADD CONSTRAINT` da migration.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import create_async_engine

    async with _cliente() as c:
        auth = await _conta(c)
        r = await c.post(
            "/api/v1/calendar/events",
            json={
                "title": "alvo",
                "start": "2026-11-12T08:00:00-03:00",
                "end": "2026-11-12T09:00:00-03:00",
            },
            headers=auth,
        )
        assert r.status_code == 201, r.text
        evento_id = r.json()["id"]
        me = await c.get("/api/v1/auth/me", headers=auth)
        user_id = me.json()["id"]

    engine = create_async_engine(
        os.getenv("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
    )
    try:
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                # O RLS esconde a linha de quem não declara o usuário, e aí
                # o UPDATE passaria sem tocar nada: declarar é o que faz
                # o teste chegar ao CHECK.
                await conn.execute(
                    text("SELECT set_config('app.user_id', :u, true)"),
                    {"u": user_id},
                )
                resultado = await conn.execute(
                    text(
                        "UPDATE core.local_events SET color = 'rosa-choque' "
                        "WHERE id = :id"
                    ),
                    {"id": evento_id},
                )
                assert resultado.rowcount == 1
    finally:
        await engine.dispose()
