from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.checkpoint.memory import InMemorySaver
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import ToolNode

from llm.tools import search_web_tool

import os
from dotenv import load_dotenv

load_dotenv()

_checkpointer: AsyncPostgresSaver | None = None

def route_from_main(state):
    last_msg = state["messages"][-1]

    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"

    return "end"

def route_from_tools(state):
    messages = state["messages"]
    # Walk back to find which agent invoked the tool
    for msg in reversed(messages):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            # Check the name or role of the message before the tool result
            if hasattr(msg, "name") and msg.name == "main_agent":
                return "main_agent"
            break
    return "end"

####################################################################################################

def get_checkpointer(pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = AsyncPostgresSaver(pool)
        return _checkpointer

def build_graph(main_agent, checkpointer):
    graph = StateGraph(MessagesState)

    graph.add_node("main_agent", main_agent)
    graph.add_node("tools", ToolNode([search_web_tool]))

    graph.add_edge(START, "main_agent")
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

    return graph.compile(checkpointer=checkpointer)