"""A conversa de 25/10: 'to afim de assistir os 3 primeiros...'."""

import asyncio
from uuid import UUID

from sqlalchemy import text

from app.core.db import user_session  # type: ignore

UID = "5e7588b9-1730-45db-9f97-9437b951e3e0"


async def go() -> None:
    async with user_session(UUID(UID)) as db:
        r = await db.execute(
            text(
                """
                SELECT id, title FROM core.conversations
                WHERE user_id = :u AND title LIKE 'porra to afim%'
                """
            ),
            {"u": UID},
        )
        for cid, title in r:
            print("=" * 70)
            print(title)
            print("=" * 70)
            m = await db.execute(
                text(
                    """
                    SELECT role, content FROM core.messages
                    WHERE conversation_id = :c
                      AND role IN ('user', 'assistant')
                    ORDER BY created_at
                    """
                ),
                {"c": cid},
            )
            for role, content in m:
                if not content:
                    continue
                print(f"\n--- {role} ---")
                print(content[:2500])


asyncio.run(go())
