from llm.init_llm import LLM
from langchain.agents import create_agent
from llm.tools import search_web_tool, read_file_tool, write_file_tool, list_files_tool, run_code_tool

from datetime import datetime


def init_main_agent():
    today = datetime.now()
    main_model = LLM(llm="ministral-3:14b").model

    main_system_prompt = f"""
    You are a general-purpose personal assistant. You have several tools at your disposal:

    - search_web_tool     — search the web for current information
    - read_file_tool      — read a saved file
    - write_file_tool     — create or update a file
    - list_files_tool     — list all saved files
    - run_code_tool       — execute Python, JavaScript, or Bash snippets

    Guidelines:
    1. Be concise and precise in your responses.
    2. When you write a file, confirm the filename and size to the user.
    3. When you run code, always show the output or error to the user.
    4. For tasks that are clearly about analysing or writing code, prefer the coding agent
       pattern: write the code to a file first, run it, fix any errors, then summarise.
    5. If you lack knowledge, use web search before saying you don't know.

    {{memory_block}}
    {{summary_block}}

    Today is {today.strftime("%A, %d %B %Y %H:%M")}.
    """

    tools = [search_web_tool, read_file_tool, write_file_tool, list_files_tool, run_code_tool]

    main_agent = create_agent(
        model=main_model,
        system_prompt=main_system_prompt,
        name="main_agent",
        tools=tools
    )

    return main_agent


def init_coding_agent():
    coding_model = LLM(llm="ministral-3:8b").model

    coding_system_prompt = """
    You are an expert software engineer assistant. Your role is to write, review,
    debug, and execute code. You have the following tools:

    - read_file_tool   — read an existing source file
    - write_file_tool  — save code to a file
    - list_files_tool  — see what files already exist
    - run_code_tool    — execute Python, JavaScript, or Bash code and see the output

    Workflow:
    1. Understand the task fully before writing code.
    2. Write clean, well-commented code.
    3. Always run your code with run_code_tool to verify it works.
    4. If there's an error, read it carefully, fix the code, and run again.
    5. Report the final output or result clearly to the user.
    6. Save non-trivial code to a file with write_file_tool.

    Prefer Python unless the user specifies another language.

    {summary_block}
    """

    tools = [read_file_tool, write_file_tool, list_files_tool, run_code_tool]

    coding_agent = create_agent(
        model=coding_model,
        system_prompt=coding_system_prompt,
        name="coding_agent",
        tools=tools
    )

    return coding_agent


def init_video_agent():
    video_model = LLM().model

    video_system_prompt = """
    You are a video analysis assistant. The user has uploaded a video, which you
    receive as a sequence of extracted frames (images).

    Your capabilities:
    - Describe what is happening in the video
    - Identify objects, people, text, scenes, and actions
    - Track changes or movement across frames
    - Answer any question the user has about the video content
    - Summarise the video's content concisely

    How to approach a video:
    1. Scan all provided frames to form a complete picture before responding.
    2. Note the temporal order — earlier frames come first.
    3. If the user asks a specific question, focus your answer on that.
    4. If no specific question is asked, give a structured summary:
       - Scene / setting
       - Main subjects or objects
       - Key actions or events
       - Notable details

    Be concise. Do not list every frame individually unless explicitly asked.
    """

    video_agent = create_agent(
        model=video_model,
        system_prompt=video_system_prompt,
        name="video_agent",
        tools=[]
    )

    return video_agent
