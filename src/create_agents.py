from llm.init_llm import LLM
from langchain.agents import create_agent
from llm.context import context_middleware
from llm.orchestration import coding_delegate, tool_progress
from config import MAIN_MODEL, CODING_MODEL, VIDEO_MODEL, OLLAMA_VIDEO_NUM_CTX
from llm.tools import search_web_tool, read_file_tool, write_file_tool, list_files_tool, run_code_tool

from datetime import datetime


# ─── Base system prompts (no context placeholders) ────────────────────────────

MAIN_AGENT_BASE_PROMPT = """\
You are a general-purpose personal assistant. You have several tools at your disposal:

- search_web_tool     — search the web for current information
- read_file_tool      — read a saved file
- write_file_tool     — create or update a file
- list_files_tool     — list all saved files
- delegate_to_coding  — delegate programming, debugging and code execution to a specialist

Guidelines:
1. Be concise and precise in your responses.
2. When you write a file, confirm the filename and size to the user.
3. When you run code, always show the output or error to the user.
4. For programming, debugging or code execution, call delegate_to_coding with a
   self-contained objective and acceptance criteria. Do not ask the user to select
   an agent. Use the specialist's report to answer, preserving useful code and results.
   Do not repeat a rejected action or delegate it again unless the user requests it.
5. If you lack knowledge, use web search before saying you don't know.
6. For news from the last seven days, search with topic='news' and time_range='week'.
   After receiving search results, summarise them in the user's language with source links.\
"""

CODING_AGENT_BASE_PROMPT = """\
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

Prefer Python unless the user specifies another language.\
"""

VIDEO_AGENT_BASE_PROMPT = """\
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

Be concise. Do not list every frame individually unless explicitly asked.\
"""


# ─── Dynamic prompt builders ──────────────────────────────────────────────────

def build_main_prompt(memory_block: str = "", summary_block: str = "") -> str:
    today = datetime.now().strftime("%A, %d %B %Y %H:%M")
    parts = [MAIN_AGENT_BASE_PROMPT]
    if memory_block.strip():
        parts.append(memory_block.strip())
    if summary_block.strip():
        parts.append(summary_block.strip())
    parts.append(f"Today is {today}.")
    return "\n\n".join(parts)


def build_coding_prompt(memory_block: str = "", summary_block: str = "") -> str:
    parts = [CODING_AGENT_BASE_PROMPT]
    if memory_block.strip():
        parts.append(memory_block.strip())
    if summary_block.strip():
        parts.append(summary_block.strip())
    return "\n\n".join(parts)


def build_video_prompt(memory_block: str = "", summary_block: str = "") -> str:
    return "\n\n".join(part.strip() for part in
                       [VIDEO_AGENT_BASE_PROMPT, memory_block, summary_block] if part.strip())


# ─── Agent factories ──────────────────────────────────────────────────────────

def init_main_agent(coding_agent=None):
    main_model = LLM(llm=MAIN_MODEL).model
    tools = [search_web_tool, read_file_tool, write_file_tool, list_files_tool,
             coding_delegate(coding_agent if coding_agent is not None else init_coding_agent())]

    # Base prompt only — context is injected at invocation time in graph.py
    main_agent = create_agent(
        model=main_model,
        system_prompt=MAIN_AGENT_BASE_PROMPT,
        name="main_agent",
        tools=tools,
        middleware=[context_middleware(build_main_prompt), tool_progress('main_agent')],
        checkpointer=True,
    )
    return main_agent


def init_coding_agent():
    coding_model = LLM(llm=CODING_MODEL).model
    tools = [read_file_tool, write_file_tool, list_files_tool, run_code_tool]

    coding_agent = create_agent(
        model=coding_model,
        system_prompt=CODING_AGENT_BASE_PROMPT,
        name="coding_agent",
        tools=tools,
        middleware=[context_middleware(build_coding_prompt), tool_progress('coding_agent')],
        checkpointer=True,
    )
    return coding_agent


def init_video_agent():
    video_model = LLM(llm=VIDEO_MODEL, num_ctx=OLLAMA_VIDEO_NUM_CTX).model

    video_agent = create_agent(
        model=video_model,
        system_prompt=VIDEO_AGENT_BASE_PROMPT,
        name="video_agent",
        tools=[],
        middleware=[context_middleware(build_video_prompt)],
    )
    return video_agent
