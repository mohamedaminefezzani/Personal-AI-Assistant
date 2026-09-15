"""
Three-layer context management
──────────────────────────────
Layer 1 — Long-term memory   : user_memory key/value table (injected by main.py, unchanged)
Layer 2 — Rolling summary    : compressed history of older turns (conversation_summaries table)
Layer 3 — Recent messages    : last N verbatim messages kept in LangGraph state

Token thresholds (per agent):
  main_agent   → summarise when history > 24 000 tokens, keep last 10 messages
  coding_agent → summarise when history > 16 000 tokens, keep last 6 messages
  video_agent  → no summarisation (single-turn by nature)

Estimation: Mistral-family averages ~4 chars/token (conservative).
"""

from __future__ import annotations

from typing import Optional
from langchain_core.messages import BaseMessage, RemoveMessage, HumanMessage, AIMessage, SystemMessage

# ─── Config ───────────────────────────────────────────────────────────────────

AGENT_CONFIG = {
    "main_agent": {
        "token_threshold": 24_000,
        "recent_keep":     10,
    },
    "coding_agent": {
        "token_threshold": 16_000,
        "recent_keep":     6,
    },
    "video_agent": {
        "token_threshold": None,   # never summarise
        "recent_keep":     None,
    },
}

CHARS_PER_TOKEN = 4   # conservative Mistral estimate

# ─── Token estimation ─────────────────────────────────────────────────────────

def _msg_text(msg: BaseMessage) -> str:
    """Extract plain text from a message regardless of content type."""
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        parts = []
        for block in msg.content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)

def messages_token_count(messages: list[BaseMessage]) -> int:
    return sum(estimate_tokens(_msg_text(m)) for m in messages)

# ─── Summary DB helpers ───────────────────────────────────────────────────────

async def load_summary(pool, thread_id: str) -> Optional[str]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT summary FROM conversation_summaries WHERE thread_id = %s",
                (thread_id,)
            )
            row = await cur.fetchone()
    return row[0] if row else None

async def save_summary(pool, thread_id: str, summary: str, up_to_msg: int):
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO conversation_summaries (thread_id, summary, up_to_msg)
               VALUES (%s, %s, %s)
               ON CONFLICT (thread_id) DO UPDATE
               SET summary = EXCLUDED.summary,
                   up_to_msg = EXCLUDED.up_to_msg,
                   updated_at = NOW()""",
            (thread_id, summary, up_to_msg)
        )

# ─── Summarisation call ───────────────────────────────────────────────────────

async def summarise_messages(messages: list[BaseMessage], llm) -> str:
    """
    Ask the LLM to compress a list of messages into a summary paragraph.
    Uses the main_agent model (14b) regardless of which agent triggered this.
    """
    lines = []
    for m in messages:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        text = _msg_text(m)
        if text.strip():
            # Truncate very long individual messages (e.g. huge file uploads)
            if len(text) > 4000:
                text = text[:4000] + "\n[… truncated for summary …]"
            lines.append(f"{role}: {text}")

    transcript = "\n\n".join(lines)

    prompt = (
        "You are a conversation summariser. "
        "Produce a concise but complete summary of the conversation below. "
        "Capture: the user's goals, key decisions made, code or files created, "
        "errors encountered and how they were resolved, and any important facts. "
        "Write in third-person past tense. Be dense — every sentence should carry information.\n\n"
        f"<conversation>\n{transcript}\n</conversation>\n\n"
        "Summary:"
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    return response.content.strip()

# ─── Main entry point ─────────────────────────────────────────────────────────

async def maybe_summarise(
    agent_name: str,
    messages: list[BaseMessage],
    thread_id: str,
    pool,
    summariser_llm,
) -> tuple[list[BaseMessage], list[str]]:
    """
    Check if the message history exceeds the token threshold for this agent.
    If so:
      1. Build a summary of all messages except the most recent `recent_keep`
      2. Persist the summary to Postgres
      3. Return the IDs of messages to remove from LangGraph state

    Returns:
        (updated_messages, ids_to_remove)
        updated_messages  — messages after pruning (same list if no pruning needed)
        ids_to_remove     — list of LangGraph message IDs to delete from state
    """
    cfg = AGENT_CONFIG.get(agent_name, AGENT_CONFIG["main_agent"])

    if cfg["token_threshold"] is None:
        return messages, []

    total_tokens = messages_token_count(messages)

    if total_tokens <= cfg["token_threshold"]:
        return messages, []

    recent_keep = cfg["recent_keep"]

    # Need at least recent_keep + 1 messages to be worth summarising
    if len(messages) <= recent_keep:
        return messages, []

    to_summarise = messages[:-recent_keep]
    to_keep      = messages[-recent_keep:]

    # Load existing summary and prepend it so we don't lose older history
    existing = await load_summary(pool, thread_id)
    if existing:
        prefix = f"[Earlier summary]\n{existing}\n\n[Continuation]"
        # Inject existing summary as a fake system message for context
        to_summarise = [SystemMessage(content=prefix)] + to_summarise

    new_summary = await summarise_messages(to_summarise, summariser_llm)
    await save_summary(pool, thread_id, new_summary, up_to_msg=len(messages) - recent_keep)

    ids_to_remove = [m.id for m in to_summarise if hasattr(m, "id") and m.id and not isinstance(m, SystemMessage)]

    return to_keep, ids_to_remove

# ─── Summary injection ────────────────────────────────────────────────────────

async def build_summary_block(pool, thread_id: str) -> str:
    """
    Load the rolling summary for a thread and format it for injection
    into the system prompt. Returns empty string if no summary exists yet.
    """
    summary = await load_summary(pool, thread_id)
    if not summary:
        return ""
    return f"\n\n## Earlier in this conversation\n{summary}"
