"""Todas as conversas da conta de teste, para achar a dos filmes."""

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
                SELECT id, title, created_at
                FROM core.conversations
                WHERE user_id = :u
                ORDER BY created_at
                """
            ),
            {"u": UID},
        )
        convs = list(r)
        print(f"=== {len(convs)} conversas ===")
        for cid, title, criada in convs:
            print(f"  {criada:%d/%m %H:%M}  {title}")

        for cid, title, _ in convs:
            r = await db.execute(
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
            linhas = list(r)
            texto = " ".join((c or "") for _, c in linhas).lower()
            if not any(
                k in texto
                for k in ("pixar", "toy story", "inseto", "filme")
            ):
                continue
            print(f"\n{'=' * 70}")
            print(f"CONVERSA: {title}")
            print("=" * 70)
            for role, content in linhas:
                if not content:
                    continue
                print(f"\n--- {role} ---")
                print(content[:2200])


asyncio.run(go())
