from llm.init_llm import LLM
from llm.tools import *
from llm.graph import *

from deepagents import create_deep_agent

from rich.console import Console
from rich.markdown import Markdown

from datetime import datetime
import asyncio


# Initialize the agents
today = datetime.now().strftime("%A, %B %d, %Y")
main_model = LLM().model # ministral 3 3b
coder_model = LLM(llm="codellama:7b").model

main_system_prompt = f"""
You are a helpful assistant. 
- For general questions, answer directly.
- For coding tasks, delegate to the coder_agent subagent.

Today is {today}
"""

coder_system_prompt = f"""
You are a coder assistant that can write code in most programming languages.
Make sure the code produces no errors as you do not have access to a sandbox for testing purposes.
Today is {today}.
"""

tools = [search_web_tool]

coder_agent = {
    "name": "coder_agent",
    "description": "Specialized agent for writing and reviewing code in any programming language.",
    "system_prompt": coder_system_prompt,
    "model": coder_model
}

subagents = [coder_agent]

main_agent = create_deep_agent(
    model=main_model,
    system_prompt=main_system_prompt,
    name="main_agent",
    tools=tools,
    subagents=subagents
)

def astream_response(message: str):
    inputs = {
        "messages": [
            {"role": "user", "content": message}
        ]
    }

    result = main_agent.invoke(inputs)
    return result


async def main():
    console = Console()
    while True:
        message = input("\nYour question: ")
        if message.lower() == "q":
            break

        console.print(Markdown(str(astream_response(message))))

if __name__ == "__main__":
    asyncio.run(main())