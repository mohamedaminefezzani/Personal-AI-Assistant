"""Postgres-backed jobs with atomic claims, expiring leases and bounded execution."""

import asyncio
from contextlib import suppress
import logging
import uuid

from fastapi import HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from errors import ContextBudgetExceeded, ConversationSummaryError
from config import TASK_TIMEOUT_SECONDS
from orchestration import WaitingApproval, pause_task


logger = logging.getLogger(__name__)
TASK_TIMEOUT = TASK_TIMEOUT_SECONDS


async def require_conversation(conn, thread_id, user_id, *, lock=False):
    cursor = await conn.execute(
        "SELECT 1 FROM conversations WHERE thread_id = %s AND user_id = %s"
        + (" FOR UPDATE" if lock else ""), (thread_id, user_id),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Conversation not found")


async def enqueue(pool, thread_id, user_id, payload):
    task_id = str(uuid.uuid4())
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                await require_conversation(conn, thread_id, user_id, lock=True)
                await conn.execute(
                    """INSERT INTO task_runs (task_id, thread_id, user_id, status, input_payload)
                       VALUES (%s, %s, %s, 'queued', %s)""",
                    (task_id, thread_id, user_id, Jsonb(payload)),
                )
    except UniqueViolation:
        raise HTTPException(status_code=409, detail="A generation is active or waiting for approval in this conversation")
    return task_id


async def recover_stale(pool):
    async with pool.connection() as conn:
        await conn.execute(
            """UPDATE task_runs SET status = 'error',
                   error = 'Worker interrupted; please send your message again',
                   input_payload = NULL, updated_at = NOW()
               WHERE status = 'running' AND lease_expires_at < NOW()"""
        )


async def claim(pool, worker_id):
    async with pool.connection() as conn:
        cursor = await conn.execute(
            """UPDATE task_runs SET status = 'running', worker_id = %s,
                   lease_expires_at = NOW() + INTERVAL '60 seconds', updated_at = NOW()
               WHERE task_id = (
                   SELECT task_id FROM task_runs WHERE status = 'queued'
                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
               ) RETURNING task_id, thread_id, user_id, input_payload""", (worker_id,),
        )
        return await cursor.fetchone()


async def finish(pool, task_id, worker_id, *, output=None, error=None):
    async with pool.connection() as conn:
        await conn.execute(
            """UPDATE task_runs SET status = %s, output = %s, error = %s,
                   input_payload = NULL, lease_expires_at = NULL, updated_at = NOW()
               WHERE task_id = %s AND worker_id = %s AND status = 'running'""",
            ("error" if error else "done", output, error, task_id, worker_id),
        )


async def heartbeat(pool, task_id, worker_id, execution):
    try:
        while True:
            await asyncio.sleep(10)
            async with pool.connection() as conn:
                cursor = await conn.execute(
                    """UPDATE task_runs SET lease_expires_at = NOW() + INTERVAL '60 seconds',
                           updated_at = NOW()
                       WHERE task_id = %s AND worker_id = %s AND status = 'running'""",
                    (task_id, worker_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Task lease lost")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Task heartbeat failed")
        execution.cancel()


async def worker_loop(app_state, execute):
    pool = app_state.pool
    worker_id = str(uuid.uuid4())
    while True:
        try:
            await recover_stale(pool)
            row = await claim(pool, worker_id)
            if not row:
                await asyncio.sleep(1)
                continue
            task_id, thread_id, user_id, payload = row
            execution = asyncio.create_task(execute(
                app_state=app_state, task_id=task_id, thread_id=thread_id,
                user_id=str(user_id), **payload,
            ))
            pulse = asyncio.create_task(heartbeat(pool, task_id, worker_id, execution))
            try:
                output = await asyncio.wait_for(execution, timeout=TASK_TIMEOUT)
                if isinstance(output, WaitingApproval):
                    await pause_task(pool, task_id, worker_id, output.approvals)
                else:
                    await finish(pool, task_id, worker_id, output=output)
            except asyncio.TimeoutError:
                logger.warning("Task %s exceeded generation limit (%ss)", task_id, TASK_TIMEOUT)
                await finish(pool, task_id, worker_id, error=(
                    f"Generation exceeded its time limit ({TASK_TIMEOUT:g}s). "
                    "Increase TASK_TIMEOUT_SECONDS or choose a smaller model."
                ))
            except (ContextBudgetExceeded, ConversationSummaryError) as exc:
                await finish(pool, task_id, worker_id, error=str(exc))
            except asyncio.CancelledError:
                await finish(pool, task_id, worker_id, error="Execution interrupted; please send your message again")
                if asyncio.current_task().cancelling():
                    raise
            except Exception:
                logger.exception("Generation failed for task %s", task_id)
                await finish(pool, task_id, worker_id, error="Generation failed; check the server logs")
            finally:
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Task worker failed; retrying")
            await asyncio.sleep(2)
