from langchain_ollama import ChatOllama
from typing import Optional
from config import (VIDEO_MODEL, OLLAMA_NUM_CTX, OLLAMA_NUM_PREDICT, OLLAMA_NUM_THREAD,
                    OLLAMA_HTTP_TIMEOUT_SECONDS, OLLAMA_KEEP_ALIVE)

class LLM:
    def __init__(self, llm: Optional[str] = None, temp: Optional[float] = 0.1,
                 num_ctx: Optional[int] = None, num_predict: Optional[int] = None):
        self.llm = llm or VIDEO_MODEL
        self.temp = temp
        self.model = ChatOllama(
            model=self.llm,
            temperature=self.temp,
            num_predict=num_predict or OLLAMA_NUM_PREDICT,
            num_ctx=num_ctx or OLLAMA_NUM_CTX,
            num_thread=OLLAMA_NUM_THREAD,
            keep_alive=OLLAMA_KEEP_ALIVE,
            client_kwargs={"timeout": OLLAMA_HTTP_TIMEOUT_SECONDS},
            async_client_kwargs={"timeout": OLLAMA_HTTP_TIMEOUT_SECONDS},
        )
