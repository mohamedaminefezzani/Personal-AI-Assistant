from fastapi import FastAPI, Request, HTTPException, Depends, Response
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager, suppress

from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from pathlib import Path
import asyncio
import json
import uuid
import re
import mimetypes
import base64
from langchain_core.messages import HumanMessage
from db.history import archive_messages, load_messages
from storage import safe_path, file_user
from tasks import enqueue, worker_loop, require_conversation, recover_stale
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt

from llm.graph import get_checkpointer, build_graph
from llm.responses import current_turn_answer, display_messages
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
    worker = asyncio.create_task(worker_loop(app.state, run_agent_task))
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        await close_pool()

app = FastAPI(lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path in ("/", "/static/app.js"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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
    message: str = Field(max_length=60_000)
    thread_id: str = Field(min_length=1, max_length=100)
    image: Optional[str] = Field(default=None, max_length=2_800_000)
    images: Optional[list[str]] = Field(default=None, max_length=5)
    video_frames: Optional[list[str]] = Field(default=None, max_length=16)
    video_filename: Optional[str] = Field(default=None, max_length=255)
    video_frame_count: Optional[int] = Field(default=None, ge=1, le=16)
    use_coding_agent: bool = False

    @field_validator("images", "video_frames")
    @classmethod
    def validate_images(cls, values):
        for value in values or []:
            cls.validate_image(value)
        return values

    @field_validator("image")
    @classmethod
    def validate_image(cls, value):
        if value is None:
            return value
        if len(value) > 2_800_000 or not re.match(r"^data:image/(png|jpeg|webp|gif);base64,", value):
            raise ValueError("Images must be base64 image data, at most 2 MB each")
        try:
            raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid base64 image")
        if not raw or len(raw) > 2_000_000:
            raise ValueError("Images must be nonempty and at most 2 MB each")
        return value

    @model_validator(mode="after")
    def validate_content(self):
        if self.video_frames and (self.image or self.images):
            raise ValueError("Send images or video frames, not both")
        if self.image and self.images:
            raise ValueError("Use image or images, not both")
        if not self.message.strip() and not (self.image or self.images or self.video_frames):
            raise ValueError("A message or attachment is required")
        if self.video_frames:
            self.video_frame_count = len(self.video_frames)
        return self


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
    try:
        target = safe_path(filepath, current_user["id"])
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=mime or "application/octet-stream", filename=target.name)


async def run_agent_task(app_state, task_id, thread_id, user_id, **payload):
    graph, pool = app_state.graph, app_state.pool
    message = payload["message"]
    frames = payload.get("video_frames") or []
    images = payload.get("images") or ([payload["image"]] if payload.get("image") else [])
    content = message
    if frames or images:
        content = [{"type": "text", "text": message or "Describe this video."}]
        content += [{"type": "image_url", "image_url": {"url": image}} for image in frames or images]
    human = HumanMessage(content=content, id=task_id + ":user")
    config = {
        "configurable": {
            "thread_id": thread_id, "user_id": user_id, "pool": pool,
            "summariser_llm": app_state.summariser_llm,
        },
        "callbacks": [CallbackHandler()],
        "recursion_limit": 50,
    }
    token = file_user.set(user_id)
    try:
        # Backfill surviving legacy messages before appending the new turn.
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        await archive_messages(pool, thread_id, state.values.get("messages", []) if state else [])
        await archive_messages(pool, thread_id, [human])
        if frames:
            async with pool.connection() as conn:
                await conn.execute(
                    """INSERT INTO message_videos (thread_id, message_index, message_id, filename, frame_count)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (thread_id, None, human.id,
                     payload.get("video_filename") or "video", len(frames)),
                )
        result = await graph.ainvoke({
            "messages": [human], "video_frames": frames or None,
            "use_coding_agent": payload.get("use_coding_agent", False),
        }, config)
        await archive_messages(pool, thread_id, result["messages"])
        return current_turn_answer(result["messages"], human.id)
    finally:
        file_user.reset(token)


@app.post("/chat")
async def chat(request: ChatRequest, req: Request, current_user: dict = Depends(get_current_user)):
    payload = request.model_dump(exclude={"thread_id"})
    task_id = await enqueue(req.app.state.pool, request.thread_id, current_user["id"], payload)
    return {"task_id": task_id}


@app.get("/chat/stream/{task_id}")
async def chat_stream(task_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool

    async def read_task():
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT status, output, error FROM task_runs WHERE task_id = %s AND user_id = %s",
                (task_id, current_user["id"]),
            )
            return await cursor.fetchone()

    if not await read_task():
        raise HTTPException(status_code=404, detail="Task not found")

    async def stream():
        while not await req.is_disconnected():
            await recover_stale(pool)
            row = await read_task()
            if not row:
                yield f"data: {json.dumps({'error': 'Task no longer exists', 'done': True})}\n\n"
                break
            status, output, error = row
            if status in ("done", "error"):
                event = {"content": output, "done": True} if status == "done" else {"error": error, "done": True}
                yield f"data: {json.dumps(event)}\n\n"
                break
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/chat/status/{thread_id}")
async def chat_status(thread_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    await recover_stale(pool)
    async with pool.connection() as conn:
        await require_conversation(conn, thread_id, current_user["id"])
        cursor = await conn.execute(
            """SELECT task_id, status FROM task_runs WHERE thread_id = %s AND user_id = %s
               ORDER BY created_at DESC LIMIT 1""", (thread_id, current_user["id"]),
        )
        row = await cursor.fetchone()
    return {"task_id": row[0] if row else None, "status": row[1] if row else None}


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
        await require_conversation(conn, thread_id, current_user["id"])
        await conn.execute(
            "UPDATE conversations SET title = %s WHERE thread_id = %s AND user_id = %s",
            (body.title, thread_id, current_user["id"])
        )
    return {"thread_id": thread_id, "title": body.title}

@app.delete("/conversations/{thread_id}")
async def delete_conversation(thread_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        async with conn.transaction():
            await require_conversation(conn, thread_id, current_user["id"], lock=True)
            cursor = await conn.execute(
                "SELECT 1 FROM task_runs WHERE thread_id = %s AND status IN ('queued', 'running')", (thread_id,),
            )
            if await cursor.fetchone():
                raise HTTPException(status_code=409, detail="Wait for the active generation before deleting this conversation")
            # Checkpoints have no FK to conversations. Delete them explicitly.
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "message_videos",
                          "conversation_summaries", "task_runs"):
                await conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))
            await conn.execute("DELETE FROM conversations WHERE thread_id = %s", (thread_id,))
    return {"deleted": thread_id}


@app.get("/conversations/{thread_id}/messages")
async def get_messages(thread_id: str, req: Request, current_user: dict = Depends(get_current_user)):
    pool = req.app.state.pool
    async with pool.connection() as conn:
        await require_conversation(conn, thread_id, current_user["id"])
        cursor = await conn.execute(
            "SELECT message_index, filename, frame_count, message_id FROM message_videos WHERE thread_id = %s",
            (thread_id,),
        )
        video_rows = await cursor.fetchall()
    video_meta = {r[3]: {"filename": r[1], "frame_count": r[2]} for r in video_rows if r[3]}
    legacy_video_meta = {r[0] - 1: {"filename": r[1], "frame_count": r[2]} for r in video_rows if not r[3]}
    messages = await load_messages(pool, thread_id)
    if not messages:
        state = await req.app.state.graph.aget_state({"configurable": {"thread_id": thread_id}})
        legacy = state.values.get("messages", []) if state else []
        await archive_messages(pool, thread_id, legacy)
        messages = await load_messages(pool, thread_id)

    result       = []
    human_index  = 0

    for msg in display_messages(messages):
        msg_type = getattr(msg, "type", None)
        content  = msg.content
        if msg_type not in ("human", "ai"):
            continue
        if not content:
            continue

        role = "user" if msg_type == "human" else "assistant"

        if isinstance(content, list):
            parts = []
            if role == "user" and (msg.id in video_meta or human_index in legacy_video_meta):
                meta = video_meta.get(msg.id) or legacy_video_meta[human_index]
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
