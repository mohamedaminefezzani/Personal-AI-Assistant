from langgraph.graph import StateGraph, MessagesState, START, END
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import add_messages
from typing import Annotated, Optional
from typing_extensions import TypedDict

from llm.tools import search_web_tool

import os

_checkpointer: AsyncPostgresSaver | None = None

# ─── Custom state with video_frames ──────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    video_frames: Optional[list[str]]  # base64 frames, set once per request

# ─── Routing ──────────────────────────────────────────────────────────────────

def route_entry(state: AgentState):
    """Route to biomechanics agent if video frames are present, otherwise main agent."""
    if state.get("video_frames"):
        return "biomechanics_agent"
    return "main_agent"

def route_from_main(state: AgentState):
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"
    return "end"

def route_from_tools(state: AgentState):
    messages = state["messages"]
    for msg in reversed(messages):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            if hasattr(msg, "name") and msg.name == "main_agent":
                return "main_agent"
            break
    return "end"

def route_from_bio(state: AgentState):
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "end"
    return "end"

####################################################################################################

def get_checkpointer(pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = AsyncPostgresSaver(pool)
    return _checkpointer

def build_graph(main_agent, biomechanics_agent, checkpointer):
    graph = StateGraph(AgentState)

    graph.add_node("main_agent", main_agent)
    graph.add_node("biomechanics_agent", biomechanics_agent)
    graph.add_node("tools", ToolNode([search_web_tool]))

    graph.add_conditional_edges(
        START,
        route_entry,
        {
            "main_agent": "main_agent",
            "biomechanics_agent": "biomechanics_agent",
        }
    )

    graph.add_conditional_edges(
        "main_agent",
        route_from_main,
        {
            "tools": "tools",
            "end": END,
        }
    )

    graph.add_conditional_edges(
        "tools",
        route_from_tools,
        {
            "main_agent": "main_agent",
        }
    )

    graph.add_conditional_edges(
        "biomechanics_agent",
        route_from_bio,
        {
            "end": END,
        }
    )

    return graph.compile(checkpointer=checkpointer)
