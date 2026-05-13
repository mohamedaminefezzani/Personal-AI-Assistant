from llm.init_llm import LLM
from langchain.agents import create_agent
from llm.tools import search_web_tool

from datetime import datetime

def init_main_agent():
    today = datetime.now()
    main_model = LLM(llm="ministral-3:14b").model

    main_system_prompt = f"""
    You are a main assistant to help with general tasks. You have various tools at your disposal
    1- Be concise and precise.

    Today is {today}
    """

    tools = [search_web_tool]

    main_agent = create_agent(
        model=main_model,
        system_prompt=main_system_prompt,
        name="main_agent",
        tools=tools
    )

    return main_agent