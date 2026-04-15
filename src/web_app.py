from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional
from pydantic import BaseModel
from pathlib import Path
import json

from llm.init_llm import LLM
from llm.tools import search_web_tool
from llm.graph import build_graph

from langchain.agents import create_agent
from datetime import datetime

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
# Mount static files (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize the agent
today = datetime.now()
model = LLM().model

agent = create_agent(
    model=model,
    tools=[search_web_tool],
    system_prompt=f"You're a helpful assistant with access to tools. Today is {today}. Do not disclose sensitive information (APIs for example)."
)

graph = build_graph(agent=agent)

class ChatRequest(BaseModel):
    message: str
    thread_id: str
    image: Optional[str] = None

async def stream_agent_response(message: str, image_data: Optional[str], thread_id: str):
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
        async for chunk in graph.astream(inputs, stream_mode="updates", config=config):
            if 'agent' in chunk:
                outputs = chunk['agent']['messages']
                last_message = outputs[-1]
                
                # Yield the content as server-sent events
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
async def chat(request: ChatRequest):
    """Handle chat messages and stream responses"""
    return StreamingResponse(
        stream_agent_response(request.message, request.image, request.thread_id),
        media_type="text/event-stream"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
