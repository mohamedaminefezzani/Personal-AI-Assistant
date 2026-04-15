from llm.init_llm import LLM
from llm.tools import *
from llm.graph import *

from langchain.agents import create_agent

from rich.console import Console
from rich.markdown import Markdown

from datetime import datetime
import asyncio


today = datetime.now()

# Initialize model with tools & build graph
model = LLM().model

agent = create_agent(
    model=model,
    tools=[search_web_tool],
    system_prompt=f"You're a helpful assistant with access to tools. Today is {today}. Do not disclose sensitive information (APIs for example)."
)

graph = build_graph(agent=agent)
#graph.get_graph().draw_ascii()

config = {
    "configurable": {"thread_id": "1"}
}

async def astream_response(message, config=config):
    inputs = {
        "messages": [
            {"role": "user", "content": message}
        ]
    }

    async for chunk in graph.astream(inputs, stream_mode="updates", config=config):
        outputs = chunk['agent']['messages']
        last_message = outputs[-1]
        yield last_message.content


async def main():
    console = Console()
    while True:
        message = input("\nYour question: ")
        if message.lower() == "q":
            break

        async for r in astream_response(message=message):
            console.print(Markdown(r))

if __name__ == "__main__":
    asyncio.run(main())