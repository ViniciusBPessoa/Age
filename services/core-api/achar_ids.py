"""Os ids que o core-api recusou: o que eram de verdade?"""

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


async def go() -> None:
    async with user_session(UUID(UID)) as db:
        for tabela, campo in [
            ("core.local_events", "id"),
            ("ai.plan_items", "id"),
            ("ai.plan_drafts", "id"),
            ("core.plans", "id"),
        ]:
            try:
                r = await db.execute(
                    text(
                        f"SELECT CAST({campo} AS text) FROM {tabela} "
                        f"WHERE CAST({campo} AS text) = ANY(:ids)"
                    ),
                    {"ids": IDS},
                )
                achados = [row[0] for row in r]
                print(f"{tabela:22} -> {len(achados)} achado(s) {achados}")
            except Exception as exc:
                print(f"{tabela:22} -> erro: {str(exc)[:90]}")

        print("\n=== planos recentes e seus itens ===")
        r = await db.execute(
            text(
                """
                SELECT CAST(p.id AS text), p.status, p.created_at,
                       count(i.id)
                FROM core.plans p
                LEFT JOIN core.plan_items i ON i.plan_id = p.id
                WHERE p.user_id = :u
                GROUP BY p.id, p.status, p.created_at
                ORDER BY p.created_at DESC LIMIT 8
                """
            ),
            {"u": UID},
        )
        for pid, status, criado, n in r:
            print(f"  {criado:%d/%m %H:%M}  {status:10} {n} itens  {pid[:8]}")


asyncio.run(go())
