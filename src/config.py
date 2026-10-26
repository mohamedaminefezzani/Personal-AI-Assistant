"""Local inference settings, loaded before agents and workers are constructed."""

import os
from pathlib import Path
from dotenv import load_dotenv


load_dotenv(Path(__file__).parent / '.env')


def positive_int(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f'{name} must be a positive integer')
    return value


TASK_TIMEOUT_SECONDS = positive_int('TASK_TIMEOUT_SECONDS', 900)
OLLAMA_HTTP_TIMEOUT_SECONDS = positive_int('OLLAMA_HTTP_TIMEOUT_SECONDS', TASK_TIMEOUT_SECONDS)
OLLAMA_NUM_CTX = positive_int('OLLAMA_NUM_CTX', 8192)
OLLAMA_VIDEO_NUM_CTX = positive_int('OLLAMA_VIDEO_NUM_CTX', 32768)
OLLAMA_NUM_PREDICT = positive_int('OLLAMA_NUM_PREDICT', 1024)
OLLAMA_NUM_THREAD = positive_int('OLLAMA_NUM_THREAD', 6)
OLLAMA_KEEP_ALIVE = os.getenv('OLLAMA_KEEP_ALIVE', '5m')
MAIN_MODEL = os.getenv('MAIN_MODEL', 'qwen3.5:9b')
CODING_MODEL = os.getenv('CODING_MODEL', 'qwen3.5:4b')
VIDEO_MODEL = os.getenv('VIDEO_MODEL', 'ministral-3:3b')
SUMMARY_MODEL = os.getenv('SUMMARY_MODEL', 'ministral-3:3b')
SUMMARY_NUM_PREDICT = positive_int('SUMMARY_NUM_PREDICT', 512)
SUMMARY_TIMEOUT_SECONDS = positive_int('SUMMARY_TIMEOUT_SECONDS', 240)
SUMMARY_SAVE_TIMEOUT_SECONDS = positive_int('SUMMARY_SAVE_TIMEOUT_SECONDS', 20)
SEARCH_MAX_RESULTS = min(5, positive_int('SEARCH_MAX_RESULTS', 3))
SEARCH_RESULT_CHARS = positive_int('SEARCH_RESULT_CHARS', 1200)

if min(OLLAMA_NUM_CTX, OLLAMA_VIDEO_NUM_CTX) <= OLLAMA_NUM_PREDICT + 1024:
    raise ValueError('Ollama context must leave at least 1024 tokens beyond the output limit')
