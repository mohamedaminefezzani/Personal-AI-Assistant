from typing import List, Optional
import socket
import os

from langchain.tools import tool
from langchain_tavily import TavilySearch

# APIs
tavily_api = os.environ["TAVILY_API_KEY"]

# Check internet connected
def is_connected():
    try:
        sock = socket.create_connection(("www.google.com", 80))
        if sock is not None:
            sock.close()
        return True
    except OSError:
        pass
    return False

@tool
def search_web_tool(query: str, max_results: Optional[int] = 5, topic: Optional[str] = "general") -> str:
    """
    Search the web for current or external information.

    Use this tool when the user's question requires up-to-date facts, recent events,
    or information not likely to be in the model's training data.
    If unable to perform the operation (offline for example), you may
    state that you are unable to access the internet.

    Args:
        query: The search query to look up on the web.
        max_results: Number of search results to return. Use a small number for focused
            lookups and a larger number for broader research. Defaults to 5.
        topic: Type of topic to search for. It can be 'general', 'news' or 'finance'.
    """

    if not is_connected():
        return "An internet connection is required to perform the web search."

    tavily_search_tool = TavilySearch(
        max_results=max_results,
        topic=topic,
        search_depth="advanced"
    )
    
    results = tavily_search_tool.invoke(query)

    return str(results)