from typing import List, Optional
import os

from langchain.tools import tool
from langchain_tavily import TavilySearch

# APIs
tavily_api = os.environ["TAVILY_API_KEY"]

@tool
def search_web_tool(query: str, max_results: Optional[int] = 5, topic: Optional[str] = "general") -> str:
    """
    Search the web for current or external information.

    Use this tool when the user's question requires up-to-date facts, recent events,
    or information not likely to be in the model's training data.

    Args:
        query: The search query to look up on the web.
        max_results: Number of search results to return. Use a small number for focused
            lookups and a larger number for broader research. Defaults to 5.
        topic: Type of topic to search for. It can be 'general', 'news' or 'finance'.
    """

    tavily_search_tool = TavilySearch(
        max_results=max_results,
        topic=topic,
        search_depth="advanced"
    )
    
    results = tavily_search_tool.invoke(query)

    return str(results)