from fastapi import FastAPI, Request, HTTPException, Depends, Response
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from typing import Optional
from pydantic import BaseModel
from pathlib import Path
import json
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt

from llm.init_llm import LLM
from llm.graph import get_checkpointer, build_graph
from llm.tools import search_web_tool
from llm.db import get_pool, close_pool, init_db

from langchain.agents import create_agent

import os
from dotenv import load_dotenv
load_dotenv()

# ─── JWT config ───────────────────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

# ─── Agents ───────────────────────────────────────────────────────────────────

today = datetime.now()
main_model = LLM().model
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

# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    await init_db(pool)
    checkpointer = get_checkpointer(pool)
    await checkpointer.setup()
    app.state.graph = build_graph(main_agent, checkpointer)
    app.state.pool = pool
    yield
    await close_pool()

app = FastAPI(lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Token helpers ────────────────────────────────────────────────────────────

def create_access_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token() -> str:
    return str(uuid.uuid4())

async def get_current_user(req: Request) -> dict:
    token = req.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {"id": payload["sub"], "username": payload["username"]}
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ─── Models ───────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    username: str
    password: str

class ChatRequest(BaseModel):
    message: str
    thread_id: str
    image: Optional[str] = None

class ConversationRequest(BaseModel):
    title: Optional[str] = "New Chat"

# ─── Auth endpoints ───────────────────────────────────────────────────────────

@app.get("/")
async def get_home():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.post("/auth/register")
async def register(body: AuthRequest, req: Request):
    pool = req.app.state.pool
    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    try:
        async with pool.connection() as conn:
            await conn.execute(
                "INSERT INTO users (username, hashed_password) VALUES (%s, %s)",
                (body.username, hashed)
            )
        return {"message": "User created"}
    except Exception:
        raise HTTPException(status_code=400, detail="Username already taken")

@app.post("/auth/login")
async def login(body: AuthRequest, req: Request, response: Response):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, hashed_password FROM users WHERE username = %s",
                (body.username,)
            )
            row = await cur.fetchone()

    if not row or not bcrypt.checkpw(body.password.encode(), row[1].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id = str(row[0])
    access_token = create_access_token(user_id, body.username)
    refresh_token = create_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO refresh_tokens (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (refresh_token, user_id, expires_at)
        )

    response.set_cookie("access_token", access_token, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    response.set_cookie("refresh_token", refresh_token, httponly=True, samesite="strict", max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400)
    return {"username": body.username}

@app.post("/auth/refresh")
async def refresh(req: Request, response: Response):
    token = req.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")

    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT rt.user_id, u.username FROM refresh_tokens rt
                   JOIN users u ON u.id = rt.user_id
                   WHERE rt.token = %s AND rt.revoked = FALSE AND rt.expires_at > NOW()""",
                (token,)
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user_id, username = str(row[0]), row[1]
    access_token = create_access_token(user_id, username)
    response.set_cookie("access_token", access_token, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    return {"username": username}

@app.post("/auth/logout")
async def logout(req: Request, response: Response):
    token = req.cookies.get("refresh_token")
    if token:
        pool = req.app.state.pool
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE token = %s",
                (token,)
            )
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"message": "Logged out"}

@app.get("/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user

# ─── Chat ─────────────────────────────────────────────────────────────────────

async def stream_agent_response(request: Request, message: str, image_data: Optional[str], thread_id: str):
    graph = request.app.state.graph

    if image_data:
        content = [
            {"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": image_data}}
        ]
    else:
        content = message

    inputs = {"messages": [{"role": "user", "content": content}]}
    config = {"configurable": {"thread_id": thread_id}}

    try:
        last_message = None
        async for chunk in graph.astream(inputs, stream_mode="updates", config=config):
            print(chunk)
            if 'main_agent' in chunk:
                outputs = chunk['main_agent']['messages']
                last_message = outputs[-1]

        if last_message:
            yield f"data: {json.dumps({'content': last_message.content})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

@app.post("/chat")
async def chat(request: ChatRequest, req: Request, current_user: dict = Depends(get_current_user)):
    return StreamingResponse(
        stream_agent_response(req, request.message, request.image, request.thread_id),
        media_type="text/event-stream"
    )

# ─── Conversations ────────────────────────────────────────────────────────────

@app.get("/conversations")
async def list_conversations(req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT thread_id, title, created_at FROM conversations WHERE user_id = %s ORDER BY created_at DESC",
                (current_user["id"],)
            )
            rows = await cur.fetchall()
            return [
                {"thread_id": row[0], "title": row[1], "created_at": row[2].isoformat()}
                for row in rows
            ]

@app.post("/conversations")
async def create_conversation(body: ConversationRequest, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    thread_id = str(uuid.uuid4())
    title = body.title or "New Chat"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO conversations (thread_id, user_id, title) VALUES (%s, %s, %s)",
            (thread_id, current_user["id"], title)
        )
    return {"thread_id": thread_id, "title": title}

@app.patch("/conversations/{thread_id}")
async def rename_conversation(thread_id: str, body: ConversationRequest, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE conversations SET title = %s WHERE thread_id = %s AND user_id = %s",
            (body.title, thread_id, current_user["id"])
        )
    return {"thread_id": thread_id, "title": body.title}

@app.delete("/conversations/{thread_id}")
async def delete_conversation(thread_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM conversations WHERE thread_id = %s AND user_id = %s",
            (thread_id, current_user["id"])
        )
    return {"deleted": thread_id}

@app.get("/conversations/{thread_id}/messages")
async def get_messages(thread_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    # Verify ownership
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM conversations WHERE thread_id = %s AND user_id = %s",
                (thread_id, current_user["id"])
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=403, detail="Forbidden")

    graph = req.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)

    if not state or not state.values.get("messages"):
        return []

    result = []
    for msg in state.values["messages"]:
        msg_type = getattr(msg, "type", None)
        content = msg.content
        if msg_type not in ("human", "ai"):
            continue
        if not content:
            continue
        if isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content = " ".join(text_parts).strip()
            if not content:
                continue
        result.append({
            "role": "user" if msg_type == "human" else "assistant",
            "content": content
        })

    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
