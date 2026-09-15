from psycopg_pool import AsyncConnectionPool

import os
from dotenv import load_dotenv

load_dotenv()  # loads .env from the current working directory

_pool: AsyncConnectionPool | None = None

async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=os.getenv("DATABASE_URL"),
            max_size=20,
            kwargs={"autocommit": True},
            open=False,
            max_idle=300,
            reconnect_timeout=30,
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
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                username TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                token TEXT PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                revoked BOOLEAN DEFAULT FALSE
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                thread_id TEXT PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS message_videos (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                thread_id TEXT NOT NULL,
                message_index INT NOT NULL,
                filename TEXT NOT NULL,
                frame_count INT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (thread_id, message_index)
            )
        """)
        # Long-term memory: key/value facts per user
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_id, key)
            )
        """)
        # Rolling conversation summaries (one per thread)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                thread_id  TEXT PRIMARY KEY,
                summary    TEXT NOT NULL,
                up_to_msg  INT  NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

async def init_task_runs(pool: AsyncConnectionPool):
    """Create the task_runs table if it doesn't exist. Called at startup."""
    async with pool.connection() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS task_runs (
                task_id     TEXT PRIMARY KEY,
                thread_id   TEXT NOT NULL,
                user_id     UUID NOT NULL,
                status      TEXT NOT NULL DEFAULT 'running',
                output      TEXT,
                error       TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                updated_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS task_runs_thread_id_idx
            ON task_runs (thread_id)
        """)
