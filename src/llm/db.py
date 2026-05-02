from psycopg_pool import AsyncConnectionPool

import os
from dotenv import load_dotenv

load_dotenv()

_pool: AsyncConnectionPool | None = None

async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=os.getenv("DATABASE_URL"),
            max_size=20,
            kwargs={"autocommit": True},
            open=False,
        )
        await _pool.open()
    return _pool

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

async def init_db(pool: AsyncConnectionPool):
    async with pool.connection() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                thread_id TEXT PRIMARY KEY,
                title TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)