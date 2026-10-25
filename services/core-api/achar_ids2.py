"""Os ids que o core-api recusou: o que eram de verdade?

Cada consulta na SUA transacao: um erro aborta o bloco inteiro no
Postgres, e a primeira tentativa perdeu as consultas seguintes.
"""

import asyncio
from uuid import UUID

from sqlalchemy import text

from app.core.db import user_session  # type: ignore

UID = "5e7588b9-1730-45db-9f97-9437b951e3e0"
IDS = [
    "c48de6af-4987-41e9-91ca-ee4ad73438f3",
    "0b518939-9a6f-4997-9c06-0886b79fe5ce",
    "efaa22f6-ab9d-48f1-80f5-be29e7c1691a",
]


async def uma(sql: str, params: dict, rotulo: str) -> None:
    try:
        async with user_session(UUID(UID)) as db:
            r = await db.execute(text(sql), params)
            linhas = list(r)
            print(f"\n--- {rotulo}: {len(linhas)} linha(s) ---")
            for row in linhas:
                print("   ", tuple(row))
    except Exception as exc:
        print(f"\n--- {rotulo}: ERRO {str(exc)[:110]}")


async def go() -> None:
    await uma(
        "SELECT CAST(id AS text), title FROM core.local_events "
        "WHERE CAST(id AS text) = ANY(:ids)",
        {"ids": IDS},
        "local_events",
    )
    await uma(
        "SELECT CAST(id AS text), target, op FROM core.plan_items "
        "WHERE CAST(id AS text) = ANY(:ids)",
        {"ids": IDS},
        "core.plan_items (por id do item)",
    )
    await uma(
        "SELECT CAST(p.id AS text), p.status, p.created_at, "
        "       count(i.id) "
        "FROM core.plans p LEFT JOIN core.plan_items i ON i.plan_id = p.id "
        "WHERE p.user_id = :u "
        "GROUP BY p.id, p.status, p.created_at "
        "ORDER BY p.created_at DESC LIMIT 10",
        {"u": UID},
        "planos recentes",
    )
    await uma(
        "SELECT CAST(i.id AS text), i.target, i.op, i.target_ref, p.status "
        "FROM core.plan_items i JOIN core.plans p ON p.id = i.plan_id "
        "WHERE p.user_id = :u AND i.target = 'event' "
        "ORDER BY p.created_at DESC LIMIT 12",
        {"u": UID},
        "itens de EVENTO recentes (target_ref = id do evento criado)",
    )


asyncio.run(go())
