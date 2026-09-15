from typing import Optional
import socket
import os
import subprocess
import tempfile
import textwrap

from langchain.tools import tool
from langchain_tavily import TavilySearch

# ─── Config ───────────────────────────────────────────────────────────────────

tavily_api = os.getenv("TAVILY_API_KEY")

# Base directory for file read/write operations.
# Defaults to ~/assistant_files — override with FILES_BASE_DIR env var.
FILES_BASE_DIR = os.path.expanduser(os.getenv("FILES_BASE_DIR", "~/assistant_files"))
os.makedirs(FILES_BASE_DIR, exist_ok=True)

# ─── Connectivity ─────────────────────────────────────────────────────────────

def is_connected():
    try:
        sock = socket.create_connection(("www.google.com", 80))
        if sock is not None:
            sock.close()
        return True
    except OSError:
        pass
    return False

# ─── Web search ───────────────────────────────────────────────────────────────

@tool
async def search_web_tool(query: str, max_results: Optional[int] = 5, topic: Optional[str] = "general") -> str:
    """
    Search the web for current or external information.

    Use this tool when the user's question requires up-to-date facts, recent events,
    or information not likely to be in the model's training data.
    If unable to perform the operation (offline for example), you may
    state that you are unable to access the internet.

    Args:
        query: The search query to look up on the web.
        max_results: Number of search results to return. Defaults to 5.
        topic: Type of topic to search for. It can be 'general', 'news' or 'finance'.
    """
    if not is_connected():
        return "An internet connection is required to perform the web search."

    tavily_search_tool = TavilySearch(
        max_results=max_results,
        topic=topic,
        search_depth="advanced"
    )

    results = await tavily_search_tool.ainvoke(query)
    return str(results)

# ─── File tools ───────────────────────────────────────────────────────────────

def _safe_path(filename: str) -> str:
    """Resolve a filename relative to FILES_BASE_DIR, blocking path traversal."""
    base = os.path.realpath(FILES_BASE_DIR)
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base + os.sep) and target != base:
        raise ValueError(f"Access denied: '{filename}' escapes the files directory.")
    return target

@tool
def read_file_tool(filename: str) -> str:
    """
    Read the contents of a file from the assistant's files directory.

    Use this when the user asks to read, view, or inspect a file they have
    previously written, or when you need to check existing file contents before
    editing. Supports any text-based file format (code, markdown, JSON, CSV, etc.).

    Args:
        filename: Name of the file to read (e.g. 'notes.md', 'src/main.py').
                  Subdirectory paths are allowed but must stay inside the base dir.
    """
    try:
        path = _safe_path(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(path):
        # List available files to help the agent
        available = []
        for root, _, files in os.walk(FILES_BASE_DIR):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), FILES_BASE_DIR)
                available.append(rel)
        hint = ("Available files: " + ", ".join(available)) if available else "No files exist yet."
        return f"File not found: '{filename}'. {hint}"

    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        size = len(content)
        if size > 30_000:
            content = content[:30_000]
            return f"[File truncated to 30 000 chars — full size: {size}]\n\n{content}"
        return content
    except Exception as e:
        return f"Error reading file: {e}"

@tool
def write_file_tool(filename: str, content: str, mode: Optional[str] = "overwrite") -> str:
    """
    Write or append content to a file in the assistant's files directory.

    Use this when the user asks you to save, create, or update a file — including
    code files, notes, configuration files, reports, etc. Parent directories are
    created automatically.

    Args:
        filename: Name of the file to write (e.g. 'hello.py', 'docs/readme.md').
        content: Text content to write into the file.
        mode: 'overwrite' (default) replaces the file; 'append' adds to the end.
    """
    try:
        path = _safe_path(filename)
    except ValueError as e:
        return str(e)

    os.makedirs(os.path.dirname(path), exist_ok=True)

    write_mode = "a" if mode == "append" else "w"
    try:
        with open(path, write_mode, encoding="utf-8") as fh:
            fh.write(content)
        action = "Appended to" if mode == "append" else "Wrote"
        return f"{action} '{filename}' ({len(content)} chars)."
    except Exception as e:
        return f"Error writing file: {e}"

@tool
def list_files_tool(subdir: Optional[str] = "") -> str:
    """
    List files available in the assistant's files directory.

    Use this before reading or writing to understand what already exists,
    or when the user asks what files are saved.

    Args:
        subdir: Optional subdirectory to list (relative to the base dir).
                Leave empty to list everything.
    """
    try:
        base = _safe_path(subdir) if subdir else FILES_BASE_DIR
    except ValueError as e:
        return str(e)

    if not os.path.exists(base):
        return "No files found."

    lines = []
    for root, dirs, files in os.walk(base):
        # skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        level = os.path.relpath(root, FILES_BASE_DIR)
        indent = "  " * (level.count(os.sep)) if level != "." else ""
        if level != ".":
            lines.append(f"{indent}📁 {os.path.basename(root)}/")
        sub_indent = "  " * (level.count(os.sep) + 1) if level != "." else "  "
        for f in sorted(files):
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            lines.append(f"{sub_indent}📄 {f}  ({size} bytes)")

    return "\n".join(lines) if lines else "No files found."

# ─── Code execution ───────────────────────────────────────────────────────────

@tool
def run_code_tool(code: str, language: Optional[str] = "python", timeout: Optional[int] = 15) -> str:
    """
    Execute a code snippet and return its stdout/stderr output.

    Use this to verify that generated code actually works, to run calculations,
    process data, or test logic. Only use after generating or reviewing code —
    never run untrusted user-supplied code directly.

    Supported languages: python, javascript (node), bash.

    Args:
        code: The source code to execute.
        language: Programming language — 'python', 'javascript', or 'bash'.
        timeout: Maximum execution time in seconds (default 15, max 60).
    """
    timeout = min(int(timeout or 15), 60)
    lang = (language or "python").lower().strip()

    runners = {
        "python": ["python3", "-c"],
        "javascript": ["node", "-e"],
        "bash": ["bash", "-c"],
    }

    if lang not in runners:
        return f"Unsupported language '{lang}'. Supported: python, javascript, bash."

    cmd_prefix = runners[lang]

    try:
        result = subprocess.run(
            cmd_prefix + [textwrap.dedent(code)],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output_parts = []
        if result.stdout.strip():
            output_parts.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            output_parts.append(f"stderr:\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"exit code: {result.returncode}")
        return "\n\n".join(output_parts) if output_parts else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Execution timed out after {timeout}s."
    except FileNotFoundError:
        return f"Runtime not found for '{lang}'. Make sure it is installed."
    except Exception as e:
        return f"Execution error: {e}"
