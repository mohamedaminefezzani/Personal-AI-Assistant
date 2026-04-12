from langchain_ollama import ChatOllama
from typing import Optional

class LLM:
    def __init__(self, llm: str = "ministral-3:3b", temp: Optional[float] = 0.1): # Default model is Ministral 3 (3b) w/0.1 temp
        self.llm = llm
        self.temp = temp
        self.model = ChatOllama(
            model=self.llm,
            temperature=self.temp
        )