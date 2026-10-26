"""Route requests to complete LangChain agents; each agent owns its tool loop."""

from typing import Annotated, Optional
from typing_extensions import TypedDict
from langchain_core.messages import SystemMessage, RemoveMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from db.history import archive_messages
from llm.context import load_summary
from orchestration import emit


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    video_frames: Optional[list[str]]
    use_coding_agent: Optional[bool]


def route_entry(state):
    if state.get('video_frames'):
        return 'video_agent'
    return 'coding_agent' if state.get('use_coding_agent') else 'main_agent'


def agent_node(agent):
    async def invoke(state, config):
        cfg = config.get('configurable', {})
        clean_state = {**state, 'messages': [m for m in state['messages'] if not isinstance(m, SystemMessage)]}
        await emit(config, 'agent_started', {'agent': agent.name}, 'agent:' + agent.name + ':start')
        # Archive old checkpoints before any context selection takes place.
        await archive_messages(cfg['pool'], cfg['thread_id'], clean_state['messages'])
        result = await agent.ainvoke(clean_state, config)
        await archive_messages(cfg['pool'], cfg['thread_id'], result['messages'])
        _, summary_until = await load_summary(cfg['pool'], cfg['thread_id'])
        messages = result['messages']
        ids = [m.id for m in messages]
        removed = [m for m in state['messages'] if isinstance(m, SystemMessage)]
        if summary_until in ids:
            boundary = ids.index(summary_until) + 1
            removed += messages[:boundary]
            messages = messages[boundary:]
        known_ids = {m.id for m in state['messages']}
        removals = [RemoveMessage(id=mid) for mid in {m.id for m in removed} if mid in known_ids]
        return {'messages': removals + messages}
    return invoke


def get_checkpointer(pool):
    return AsyncPostgresSaver(pool)


def build_graph(main_agent, coding_agent, video_agent, checkpointer):
    graph = StateGraph(AgentState)
    for name, agent in [('main_agent', main_agent), ('coding_agent', coding_agent), ('video_agent', video_agent)]:
        graph.add_node(name, agent_node(agent))
        graph.add_edge(name, END)
    graph.add_conditional_edges(START, route_entry, {
        'main_agent': 'main_agent', 'coding_agent': 'coding_agent', 'video_agent': 'video_agent',
    })
    return graph.compile(checkpointer=checkpointer)
