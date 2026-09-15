from fastapi import FastAPI, Request, HTTPException, Depends, Response
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from typing import Optional
from pydantic import BaseModel
from pathlib import Path
import asyncio
import json
import uuid
import re
import mimetypes
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt

from llm.graph import get_checkpointer, build_graph
from llm.context import build_summary_block
from db.db import get_pool, close_pool, init_db, init_task_runs
from create_agents import init_main_agent, init_coding_agent, init_video_agent
from langfuse import Langfuse, get_client
from langfuse.langchain import CallbackHandler

import os
from dotenv import load_dotenv
load_dotenv()  # loads .env from the current working directory

# ─── JWT config ───────────────────────────────────────────────────────────────

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

# ─── File base dir ────────────────────────────────────────────────────────────

FILES_BASE_DIR = os.path.expanduser(os.getenv("FILES_BASE_DIR", "~/assistant_files"))
os.makedirs(FILES_BASE_DIR, exist_ok=True)

# ─── Regex ────────────────────────────────────────────────────────────────────

FILE_BLOCK_RE = re.compile(
    r'### File: (.+?)\n```\w*\n([\s\S]+?)```',
    re.MULTILINE
)

# ─── Agents & Langfuse ────────────────────────────────────────────────────────

main_agent   = init_main_agent()
coding_agent = init_coding_agent()
video_agent  = init_video_agent()

Langfuse(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    host="https://cloud.langfuse.com",
    flush_interval=30,
    timeout=30,
)

langfuse = get_client()

# ─── In-memory subscriber registry ───────────────────────────────────────────
# task_id -> list of asyncio.Queue
# Each connected SSE client gets its own queue; the background task broadcasts to all.

_task_subscribers: dict[str, list[asyncio.Queue]] = {}


def _broadcast(task_id: str, event: dict):
    for q in _task_subscribers.get(task_id, []):
        q.put_nowait(event)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    await init_db(pool)
    await init_task_runs(pool)
    checkpointer = get_checkpointer(pool)
    await checkpointer.setup()
    app.state.graph = build_graph(main_agent, coding_agent, video_agent, checkpointer)
    app.state.pool  = pool
    # 14b model used for summarisation — best quality within the local stack
    from llm.init_llm import LLM
    app.state.summariser_llm = LLM(llm="ministral-3:14b").model
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

# ─── Pydantic models ──────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    username: str
    password: str

class ChatRequest(BaseModel):
    message: str
    thread_id: str
    image: Optional[str] = None
    images: Optional[list[str]] = None
    video_frames: Optional[list[str]] = None
    video_filename: Optional[str] = None
    video_frame_count: Optional[int] = None
    use_coding_agent: Optional[bool] = False

class ConversationRequest(BaseModel):
    title: Optional[str] = "New Chat"

class MemoryRequest(BaseModel):
    key: str
    value: str

# ─── Auth ─────────────────────────────────────────────────────────────────────

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
    access_token  = create_access_token(user_id, body.username)
    refresh_token = create_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO refresh_tokens (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (refresh_token, user_id, expires_at)
        )

    response.set_cookie("access_token",  access_token,  httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
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

# ─── Memory ───────────────────────────────────────────────────────────────────

@app.get("/memory")
async def get_memory(req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT key, value, updated_at FROM user_memory WHERE user_id = %s ORDER BY updated_at DESC",
                (current_user["id"],)
            )
            rows = await cur.fetchall()
    return [{"key": r[0], "value": r[1], "updated_at": r[2].isoformat()} for r in rows]

@app.put("/memory")
async def upsert_memory(body: MemoryRequest, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO user_memory (user_id, key, value)
               VALUES (%s, %s, %s)
               ON CONFLICT (user_id, key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = NOW()""",
            (current_user["id"], body.key, body.value)
        )
    return {"key": body.key, "value": body.value}

@app.delete("/memory/{key}")
async def delete_memory(key: str, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM user_memory WHERE user_id = %s AND key = %s",
            (current_user["id"], key)
        )
    return {"deleted": key}

# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health(req: Request):
    import socket
    checks = {}
    try:
        pool = req.app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    try:
        sock = socket.create_connection(("127.0.0.1", 11434), timeout=2)
        sock.close()
        checks["ollama"] = "ok"
    except Exception:
        checks["ollama"] = "unreachable"

    checks["tavily_key"] = "set" if os.getenv("TAVILY_API_KEY") else "missing"
    all_ok = all(v in ("ok", "set") for v in checks.values())
    return JSONResponse({"status": "ok" if all_ok else "degraded", "checks": checks},
                        status_code=200 if all_ok else 503)

# ─── File download ────────────────────────────────────────────────────────────

@app.get("/files/{filepath:path}")
async def download_file(filepath: str, current_user: dict = Depends(get_current_user)):
    base   = os.path.realpath(FILES_BASE_DIR)
    target = os.path.realpath(os.path.join(base, filepath))
    if not target.startswith(base + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(target)
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        filename=os.path.basename(target))

# ─── Background agent task ────────────────────────────────────────────────────

async def load_user_memory(pool, user_id: str) -> str:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT key, value FROM user_memory WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
                (user_id,)
            )
            rows = await cur.fetchall()
    if not rows:
        return ""
    lines = "\n".join(f"- {r[0]}: {r[1]}" for r in rows)
    return f"\n\n## What I remember about you\n{lines}"


async def run_agent_task(
    app_state,
    task_id: str,
    message: str,
    images: Optional[list[str]],
    video_frames: Optional[list[str]],
    video_filename: Optional[str],
    video_frame_count: Optional[int],
    use_coding_agent: bool,
    thread_id: str,
    user_id: str,
):
    """
    Runs entirely in a background asyncio task.
    Writes progress to task_runs in Postgres and broadcasts to any live SSE subscribers.
    """
    graph          = app_state.graph
    pool           = app_state.pool
    summariser_llm = app_state.summariser_llm

    memory_block  = await load_user_memory(pool, user_id)
    summary_block = await build_summary_block(pool, thread_id)

    # Build graph inputs
    if video_frames:
        prompt  = message or "Please describe and summarise what happens in this video."
        content = [{"type": "text", "text": prompt + memory_block}]
        for frame in video_frames:
            content.append({"type": "image_url", "image_url": {"url": frame}})
        inputs = {
            "messages":         [{"role": "user", "content": content}],
            "video_frames":     video_frames,
            "use_coding_agent": False,
        }
    else:
        all_images   = images or []
        # Inject memory + summary into the human message so agents always have context
        context_suffix = memory_block + summary_block
        full_message   = message + context_suffix if context_suffix.strip() else message
        if all_images:
            content = [{"type": "text", "text": full_message}]
            for img in all_images:
                content.append({"type": "image_url", "image_url": {"url": img}})
        else:
            content = full_message
        inputs = {
            "messages":         [{"role": "user", "content": content}],
            "video_frames":     None,
            "use_coding_agent": bool(use_coding_agent),
        }

    config = {
        "configurable": {
            "thread_id":      thread_id,
            "pool":           pool,
            "summariser_llm": summariser_llm,
        },
        "callbacks": [CallbackHandler()],
    }

    agent_keys = ("main_agent", "coding_agent", "video_agent")

    try:
        last_message = None
        async for chunk in graph.astream(inputs, stream_mode="updates", config=config):
            for key in agent_keys:
                if key in chunk:
                    last_message = chunk[key]["messages"][-1]

        output = last_message.content if last_message else ""

        # Persist video metadata
        if last_message and video_frames and video_filename and video_frame_count:
            state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            message_index = sum(
                1 for m in state.values.get("messages", [])
                if getattr(m, "type", None) == "human"
            )
            async with pool.connection() as conn:
                await conn.execute(
                    """INSERT INTO message_videos (thread_id, message_index, filename, frame_count)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (thread_id, message_index) DO NOTHING""",
                    (thread_id, message_index, video_filename, video_frame_count)
                )

        # Mark complete in DB
        async with pool.connection() as conn:
            await conn.execute(
                """UPDATE task_runs
                   SET status = 'done', output = %s, updated_at = NOW()
                   WHERE task_id = %s""",
                (output, task_id)
            )

        _broadcast(task_id, {"content": output, "done": True})

    except Exception as e:
        err = str(e)
        async with pool.connection() as conn:
            await conn.execute(
                """UPDATE task_runs
                   SET status = 'error', error = %s, updated_at = NOW()
                   WHERE task_id = %s""",
                (err, task_id)
            )
        _broadcast(task_id, {"error": err, "done": True})

    finally:
        # Clean up subscriber list for this task
        _task_subscribers.pop(task_id, None)
        try:
            langfuse.flush()
        except Exception:
            pass

# ─── Chat endpoints ───────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(request: ChatRequest, req: Request, current_user: dict = Depends(get_current_user)):
    """
    Start a generation. Returns task_id immediately.
    The actual work runs in a background asyncio task.
    """
    pool     = req.app.state.pool
    task_id  = str(uuid.uuid4())
    user_id  = current_user["id"]

    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO task_runs (task_id, thread_id, user_id, status)
               VALUES (%s, %s, %s, 'running')""",
            (task_id, request.thread_id, user_id)
        )

    # Register subscriber list before launching task (avoids a race)
    _task_subscribers[task_id] = []

    asyncio.create_task(run_agent_task(
        app_state        = req.app.state,
        task_id          = task_id,
        message          = request.message,
        images           = request.images or ([request.image] if request.image else []),
        video_frames     = request.video_frames,
        video_filename   = request.video_filename,
        video_frame_count= request.video_frame_count,
        use_coding_agent = request.use_coding_agent or False,
        thread_id        = request.thread_id,
        user_id          = user_id,
    ))

    return {"task_id": task_id}


@app.get("/chat/stream/{task_id}")
async def chat_stream(task_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    """
    SSE endpoint. Any number of tabs can connect to the same task_id.

    If the task is already done it returns the stored result immediately.
    If it's still running the client joins the live broadcast queue.
    If it errored it returns the error immediately.
    """
    pool = req.app.state.pool

    # Check existing DB state first — handles reconnects / page refresh
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, output, error, user_id FROM task_runs WHERE task_id = %s",
                (task_id,)
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Task not found")

    # Ownership check
    if str(row[3]) != current_user["id"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    status, output, error = row[0], row[1], row[2]

    async def already_done():
        if status == "done":
            yield f"data: {json.dumps({'content': output, 'done': True})}\n\n"
        else:
            yield f"data: {json.dumps({'error': error or 'Unknown error', 'done': True})}\n\n"

    if status in ("done", "error"):
        return StreamingResponse(already_done(), media_type="text/event-stream")

    # Task is still running — subscribe to live broadcasts
    queue: asyncio.Queue = asyncio.Queue()

    if task_id not in _task_subscribers:
        # Task finished between the DB read and here — re-read from DB
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT status, output, error FROM task_runs WHERE task_id = %s",
                    (task_id,)
                )
                row2 = await cur.fetchone()
        if row2 and row2[0] in ("done", "error"):
            async def late_done():
                if row2[0] == "done":
                    yield f"data: {json.dumps({'content': row2[1], 'done': True})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': row2[2] or 'Unknown error', 'done': True})}\n\n"
            return StreamingResponse(late_done(), media_type="text/event-stream")

    _task_subscribers.setdefault(task_id, []).append(queue)

    async def live_stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("done"):
                        break
                except asyncio.TimeoutError:
                    # Send a keepalive comment so the browser doesn't close the connection
                    yield ": keepalive\n\n"
        finally:
            try:
                _task_subscribers.get(task_id, []).remove(queue)
            except ValueError:
                pass

    return StreamingResponse(live_stream(), media_type="text/event-stream")


@app.get("/chat/status/{thread_id}")
async def chat_status(thread_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    """
    Returns the most recent running or done task for a thread.
    The frontend calls this on conversation switch to know whether to reconnect to a stream.
    """
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT task_id, status FROM task_runs
                   WHERE thread_id = %s AND user_id = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (thread_id, current_user["id"])
            )
            row = await cur.fetchone()

    if not row:
        return {"task_id": None, "status": None}
    return {"task_id": row[0], "status": row[1]}

# ─── Conversations ────────────────────────────────────────────────────────────

def parse_user_message(content: str) -> dict:
    if "### File:" not in content:
        return {"content": content}
    parts = []
    first_match = FILE_BLOCK_RE.search(content)
    if first_match:
        pre_text = content[:first_match.start()].strip()
        if pre_text:
            parts.append({"type": "text", "text": pre_text})
    for match in FILE_BLOCK_RE.finditer(content):
        filename = match.group(1).strip()
        file_content = match.group(2)
        parts.append({"type": "file", "name": filename, "content": file_content})
    return {"parts": parts} if parts else {"content": content}

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
    return [{"thread_id": r[0], "title": r[1], "created_at": r[2].isoformat()} for r in rows]

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
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM conversations WHERE thread_id = %s AND user_id = %s",
                (thread_id, current_user["id"])
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=403, detail="Forbidden")

            await cur.execute(
                "SELECT message_index, filename, frame_count FROM message_videos WHERE thread_id = %s",
                (thread_id,)
            )
            video_rows = await cur.fetchall()
            video_meta = {row[0]: {"filename": row[1], "frame_count": row[2]} for row in video_rows}

    graph  = req.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}
    state  = await graph.aget_state(config)

    if not state or not state.values.get("messages"):
        return []

    result       = []
    human_index  = 0

    for msg in state.values["messages"]:
        msg_type = getattr(msg, "type", None)
        content  = msg.content
        if msg_type not in ("human", "ai"):
            continue
        if not content:
            continue

        role = "user" if msg_type == "human" else "assistant"

        if isinstance(content, list):
            parts = []
            image_url_count = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image_url")

            if role == "user" and image_url_count > 5 and human_index in video_meta:
                meta = video_meta[human_index]
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip():
                        parts.append({"type": "text", "text": c["text"]})
                parts.append({
                    "type": "video",
                    "filename": meta["filename"],
                    "frame_count": meta["frame_count"]
                })
            else:
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text" and c.get("text", "").strip():
                        parts.append({"type": "text", "text": c["text"]})
                    elif c.get("type") == "image_url":
                        url = c.get("image_url", {}).get("url", "")
                        if url:
                            parts.append({"type": "image_url", "url": url})

            if not parts:
                if role == "user":
                    human_index += 1
                continue
            result.append({"role": role, "parts": parts})
        else:
            if role == "user":
                result.append({**parse_user_message(content), "role": role})
            else:
                result.append({"role": role, "content": content})

        if role == "user":
            human_index += 1

    return result

if __name__ == "__main__":
    import asyncio
    import sys
    import uvicorn

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(app, host="0.0.0.0", port=8000)
