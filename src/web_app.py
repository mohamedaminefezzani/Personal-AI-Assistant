from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from typing import Optional
from pydantic import BaseModel
from pathlib import Path
import json
import uuid

from llm.init_llm import LLM
from llm.graph import get_checkpointer, build_graph
from llm.tools import search_web_tool
from llm.db import get_pool, close_pool, init_db

from langchain.agents import create_agent
from datetime import datetime

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
    await init_db(pool)  # Create conversations table if not exists
    checkpointer = get_checkpointer(pool)
    await checkpointer.setup()
    app.state.graph = build_graph(main_agent, checkpointer)
    app.state.pool = pool
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

class ConversationRequest(BaseModel):
    title: Optional[str] = "New Chat"

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

####################################################################################################
# Conversation endpoints

@app.get("/conversations")
async def list_conversations(req: Request):
    """Return all conversations ordered by most recent first"""
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT thread_id, title, created_at FROM conversations ORDER BY created_at DESC"
            )
            rows = await cur.fetchall()
            return [
                {"thread_id": row[0], "title": row[1], "created_at": row[2].isoformat()}
                for row in rows
            ]

@app.post("/conversations")
async def create_conversation(body: ConversationRequest, req: Request):
    """Create a new conversation and return its thread_id"""
    pool = req.app.state.pool
    thread_id = str(uuid.uuid4())
    title = body.title or "New Chat"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO conversations (thread_id, title) VALUES (%s, %s)",
            (thread_id, title)
        )
    return {"thread_id": thread_id, "title": title}

@app.patch("/conversations/{thread_id}")
async def rename_conversation(thread_id: str, body: ConversationRequest, req: Request):
    """Rename a conversation"""
    pool = req.app.state.pool
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE conversations SET title = %s WHERE thread_id = %s",
            (body.title, thread_id)
        )
    return {"thread_id": thread_id, "title": body.title}

@app.delete("/conversations/{thread_id}")
async def delete_conversation(thread_id: str, req: Request):
    """Delete a conversation"""
    pool = req.app.state.pool
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM conversations WHERE thread_id = %s",
            (thread_id,)
        )
    return {"deleted": thread_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)