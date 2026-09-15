from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from langchain_core.messages import RemoveMessage
from langchain_core.runnables import RunnableConfig

from typing import Annotated, Optional
from typing_extensions import TypedDict

from llm.tools import search_web_tool, read_file_tool, write_file_tool, list_files_tool, run_code_tool
from llm.context import maybe_summarise

import os

_checkpointer: AsyncPostgresSaver | None = None

# ─── State ────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages:         Annotated[list, add_messages]
    video_frames:     Optional[list[str]]
    use_coding_agent: Optional[bool]

# ─── Routing ──────────────────────────────────────────────────────────────────

def route_entry(state: AgentState):
    if state.get("video_frames"):
        return "video_agent"
    if state.get("use_coding_agent"):
        return "coding_agent"
    return "main_agent"

def route_from_main(state: AgentState):
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "main_tools"
    return "summarise_main"

def route_from_coding(state: AgentState):
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "coding_tools"
    return "summarise_coding"

def route_from_video(state: AgentState):
    return "end"

# ─── Summarisation nodes ──────────────────────────────────────────────────────

def _make_summarise_node(agent_name: str):
    """
    Factory that returns an async node function for a given agent.
    Checks token usage and prunes + summarises if over threshold.
    Returns RemoveMessage ops so LangGraph drops old messages from state.
    Runtime deps (pool, llm, thread_id) are passed via config to avoid
    serialization errors — the checkpointer never sees them.
    """
    async def summarise_node(state: AgentState, config: RunnableConfig):
        cfg            = config.get("configurable", {})
        pool           = cfg.get("pool")
        summariser_llm = cfg.get("summariser_llm")
        thread_id      = cfg.get("thread_id")

        # If runtime deps aren't injected (shouldn't happen) skip silently
        if not pool or not summariser_llm or not thread_id:
            return {}

        messages = state["messages"]
        _, ids_to_remove = await maybe_summarise(
            agent_name     = agent_name,
            messages       = messages,
            thread_id      = thread_id,
            pool           = pool,
            summariser_llm = summariser_llm,
        )

        if not ids_to_remove:
            return {}

        # Tell LangGraph to delete those messages from the checkpointed state
        return {"messages": [RemoveMessage(id=mid) for mid in ids_to_remove]}

    summarise_node.__name__ = f"summarise_{agent_name}"
    return summarise_node

# ─── Checkpointer ─────────────────────────────────────────────────────────────

def get_checkpointer(pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = AsyncPostgresSaver(pool)
    return _checkpointer

# ─── Graph builder ────────────────────────────────────────────────────────────

def build_graph(main_agent, coding_agent, video_agent, checkpointer):
    graph = StateGraph(AgentState)

    # ── Agent nodes ────────────────────────────────────────────────────────────
    graph.add_node("main_agent",   main_agent)
    graph.add_node("coding_agent", coding_agent)
    graph.add_node("video_agent",  video_agent)

    # ── Tool nodes ─────────────────────────────────────────────────────────────
    main_tools_list   = [search_web_tool, read_file_tool, write_file_tool, list_files_tool, run_code_tool]
    coding_tools_list = [read_file_tool, write_file_tool, list_files_tool, run_code_tool]

    graph.add_node("main_tools",   ToolNode(main_tools_list))
    graph.add_node("coding_tools", ToolNode(coding_tools_list))

    # ── Summarisation nodes ────────────────────────────────────────────────────
    graph.add_node("summarise_main_agent",   _make_summarise_node("main_agent"))
    graph.add_node("summarise_coding_agent", _make_summarise_node("coding_agent"))

    # ── Entry ──────────────────────────────────────────────────────────────────
    graph.add_conditional_edges(
        START,
        route_entry,
        {
            "main_agent":   "main_agent",
            "coding_agent": "coding_agent",
            "video_agent":  "video_agent",
        }
    )

    # ── Main agent loop ────────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "main_agent",
        route_from_main,
        {
            "main_tools":    "main_tools",
            "summarise_main": "summarise_main_agent",
        }
    )
    graph.add_edge("main_tools", "main_agent")
    graph.add_edge("summarise_main_agent", END)

    # ── Coding agent loop ──────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "coding_agent",
        route_from_coding,
        {
            "coding_tools":    "coding_tools",
            "summarise_coding": "summarise_coding_agent",
        }
    )
    graph.add_edge("coding_tools", "coding_agent")
    graph.add_edge("summarise_coding_agent", END)

    # ── Video agent ────────────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "video_agent",
        route_from_video,
        {"end": END}
    )

    return graph.compile(checkpointer=checkpointer)
