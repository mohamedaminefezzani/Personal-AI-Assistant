"""
Long-term memory helpers.

Provides load_user_memory, which fetches the user's key/value memory entries
from Postgres and formats them for injection into agent system prompts.
"""

from psycopg_pool import AsyncConnectionPool


async def load_user_memory(pool: AsyncConnectionPool, user_id: str) -> str:
    """
    Load up to 50 memory entries for a user and return a formatted block
    suitable for injection into a system prompt.  Returns an empty string
    if no entries exist.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT key, value FROM user_memory WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
                (user_id,),
            )
            rows = await cur.fetchall()

    if not rows:
        return ""

    lines = "\n".join(f"- {r[0]}: {r[1]}" for r in rows)
    return f"## What I remember about you\n{lines}"
