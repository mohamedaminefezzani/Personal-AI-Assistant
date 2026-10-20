"""Append-only conversation archive, independent of the model's working context."""

from langchain_core.messages import message_to_dict, messages_from_dict
from psycopg.types.json import Jsonb


async def archive_messages(pool, thread_id, messages):
    async with pool.connection() as conn:
        async with conn.transaction():
            for message in messages:
                if message.type == "system" or not message.id:
                    continue
                await conn.execute(
                    """INSERT INTO conversation_messages (thread_id, message_id, payload)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (thread_id, message_id) DO NOTHING""",
                    (thread_id, message.id, Jsonb(message_to_dict(message))),
                )


async def load_messages(pool, thread_id):
    async with pool.connection() as conn:
        cursor = await conn.execute(
            """SELECT payload FROM conversation_messages
               WHERE thread_id = %s ORDER BY sequence""", (thread_id,),
        )
        rows = await cursor.fetchall()
    return messages_from_dict([row[0] for row in rows])
