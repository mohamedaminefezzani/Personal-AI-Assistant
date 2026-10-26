"""Durable progress events and approval records for checkpointed agent runs."""

from dataclasses import dataclass
from fastapi import HTTPException
from psycopg.types.json import Jsonb


@dataclass
class WaitingApproval:
    approvals: list


async def emit(config, kind, detail, key):
    cfg = config.get('configurable', {})
    if not cfg.get('task_id'):
        return
    async with cfg['pool'].connection() as conn:
        await conn.execute(
            '''INSERT INTO task_events (task_id, event_key, payload) VALUES (%s, %s, %s)
               ON CONFLICT (task_id, event_key) DO NOTHING''',
            (cfg['task_id'], key, Jsonb({'kind': kind, **detail})),
        )


async def read_events(pool, task_id, after=0):
    async with pool.connection() as conn:
        cursor = await conn.execute(
            'SELECT sequence, payload FROM task_events WHERE task_id = %s AND sequence > %s ORDER BY sequence',
            (task_id, after),
        )
        return [{'sequence': row[0], **row[1]} for row in await cursor.fetchall()]


async def pause_task(pool, task_id, worker_id, approvals):
    async with pool.connection() as conn:
        await conn.execute(
            '''UPDATE task_runs SET status = 'waiting_approval', pending_approvals = %s,
               lease_expires_at = NULL, updated_at = NOW()
               WHERE task_id = %s AND worker_id = %s AND status = 'running' ''',
            (Jsonb(approvals), task_id, worker_id),
        )


async def decide(pool, task_id, user_id, approval_id, decision):
    async with pool.connection() as conn:
        async with conn.transaction():
            cursor = await conn.execute(
                '''SELECT status, pending_approvals FROM task_runs
                   WHERE task_id = %s AND user_id = %s FOR UPDATE''', (task_id, user_id),
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(404, 'Task not found')
            if row[0] != 'waiting_approval':
                raise HTTPException(409, 'This task is not waiting for approval')
            approval = next((a for a in row[1] if a['approval_id'] == approval_id), None)
            if approval is None:
                raise HTTPException(409, 'Approval is no longer pending')
            resume = {approval['interrupt_id']: {'approval_id': approval_id, 'decision': decision}}
            await conn.execute(
                '''UPDATE task_runs SET status = 'queued', pending_approvals = NULL, worker_id = NULL,
                   input_payload = input_payload || %s, updated_at = NOW()
                   WHERE task_id = %s''', (Jsonb({'resume': resume}), task_id),
            )
            await conn.execute(
                '''INSERT INTO task_events (task_id, event_key, payload) VALUES (%s, %s, %s)
                   ON CONFLICT (task_id, event_key) DO NOTHING''',
                (task_id, 'decision:' + approval_id,
                 Jsonb({'kind': 'approval_decided', 'tool': approval['tool'], 'decision': decision})),
            )
