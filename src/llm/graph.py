from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.checkpoint.memory import InMemorySaver

def build_graph(agent):
    graph = StateGraph(MessagesState)
    memory = InMemorySaver()

    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)

    return graph.compile(memory)