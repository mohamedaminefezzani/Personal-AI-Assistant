from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from typing import Optional
from pydantic import BaseModel
from pathlib import Path
import json

from llm.init_llm import LLM
from llm.graph import get_checkpointer, build_graph
from llm.tools import search_web_tool
from llm.db import get_pool, close_pool

from langchain.agents import create_agent
from datetime import datetime

import asyncio

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Initialize the agents
today = datetime.now()
main_model = LLM().model # ministral 3 3b
coder_model = LLM(llm="codellama:7b").model

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    checkpointer = get_checkpointer(pool)
    await checkpointer.setup()
    app.state.graph = build_graph(main_agent, checkpointer)
    yield

    await close_pool()

app = FastAPI(lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
# Mount static files (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

####################################################################################################

class ChatRequest(BaseModel):
    message: str
    thread_id: str
    image: Optional[str] = None

async def stream_agent_response(request: Request, message: str, image_data: Optional[str], thread_id: str):
    graph = request.app.state.graph
    """Stream the agent's response token by token"""
    
    # Prepare the message content
    if image_data:
        # If there's an image, create a multi-modal message
        content = [
            {"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": image_data}}
        ]
    else:
        content = message
    
    inputs = {
        "messages": [
            {"role": "user", "content": content}
        ]
    }
    
    config = {
        "configurable": {"thread_id": thread_id}
    }
    
    try:
        last_message = None
        async for chunk in graph.astream(inputs, stream_mode="updates", config=config):
            print(chunk)
            if 'main_agent' in chunk:
                outputs = chunk['main_agent']['messages']
                last_message = outputs[-1]
                
        # Yield the content as server-sent events
        if last_message:
           yield f"data: {json.dumps({'content': last_message.content})}\n\n"
        
        # Signal end of stream
        yield f"data: {json.dumps({'done': True})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

@app.get("/")
async def get_home():
    """Serve the main chat interface"""
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.post("/chat")
async def chat(request: ChatRequest, req: Request):
    """Handle chat messages and stream responses"""
    return StreamingResponse(
        stream_agent_response(req, request.message, request.image, request.thread_id),
        media_type="text/event-stream"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
