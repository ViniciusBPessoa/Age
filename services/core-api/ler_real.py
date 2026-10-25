"""Le a agenda e as tarefas da conta real, com a sessao do app (RLS)."""

import asyncio

from sqlalchemy import text

from uuid import UUID

from app.core.db import user_session  # type: ignore

UID = "5e7588b9-1730-45db-9f97-9437b951e3e0"


async def go() -> None:
    async with user_session(UUID(UID)) as db:

        print("=== EVENTOS (local_events) ===")
        r = await db.execute(
            text(
                """
                SELECT start_at, end_at, title
                FROM core.local_events
                WHERE user_id = :u AND deleted_at IS NULL
                ORDER BY start_at
                """
            ),
            {"u": UID},
        )
        for row in r:
            print(f"  {row[0]:%d/%m %a %H:%M}-{row[1]:%H:%M}  {row[2]}")

        print("\n=== TAREFAS ===")
        r = await db.execute(
            text(
                """
                SELECT title, due_date, status
                FROM core.local_tasks
                WHERE user_id = :u AND deleted_at IS NULL
                ORDER BY position
                """
            ),
            {"u": UID},
        )
        for row in r:
            print(f"  [{row[2]:6}] {str(row[1] or '-'):12} {row[0]}")

        print("\n=== MENSAGENS DA CONVERSA MAIS RECENTE ===")
        r = await db.execute(
            text(
                """
                SELECT c.id, c.title
                FROM core.conversations c
                WHERE c.user_id = :u
                ORDER BY c.created_at DESC LIMIT 1
                """
            ),
            {"u": UID},
        )
        conv = r.first()
        if conv:
            print("conversa:", conv[1])
            r = await db.execute(
                text(
                    """
                    SELECT role, content FROM core.messages
                    WHERE conversation_id = :c AND role IN ('user','assistant')
                    ORDER BY created_at
                    """
                ),
                {"c": conv[0]},
            )
            for role, content in r:
                if not content:
                    continue
                print(f"\n--- {role} ---")
                print(content[:1500])


asyncio.run(go())
