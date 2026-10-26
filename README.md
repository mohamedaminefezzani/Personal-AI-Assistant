# Personal-AI-Assistant

An ongoing personal project to build a local multi-agent AI assistant. Runs entirely on your machine via Ollama.

---

## Agents

| Agent | Model | Trigger | Tools |
|---|---|---|---|
| **main_agent** | Qwen 3.5 9b | Default for all text messages | Web search, read/write files, delegate coding |
| **coding_agent** | Qwen 3.5 4b | Delegation from main, or coding button | Read/write files, run code with approval |
| **video_agent** | Ministral 3 3b | Video file upload | None (vision only) |

---

## Features

### Orchestration and approval

The main agent can call `delegate_to_coding(objective, acceptance_criteria)` without
the user selecting coding mode. The coding specialist runs as a nested checkpointed
agent, returns a report, and the main agent produces the final answer. Specialist
delegations run sequentially; at most three are accepted per user turn. The manual
coding button remains available as a shortcut.

Read/search actions run automatically. Before `write_file_tool` or `run_code_tool`,
the backend calls LangGraph `interrupt()` and checkpoints the pending action.
The UI shows the file diff or exact code/language/options, with Approve and Reject.
Both the main and coding agents use this gate. File writes are checked against the
file version shown for approval; if it changed, the old approval cannot overwrite it.
Code still runs on the host, outside a sandbox, after explicit approval. File access
remains inside the authenticated user's assistant files directory.

Jobs transition `queued -> running -> waiting_approval -> queued -> running -> done`
(or error). Waiting releases the worker and holds no model HTTP request. Each resumed
execution segment receives the configured task deadline; waiting time is excluded.
Only the owning user can decide via `POST /chat/approval/{task_id}` with
`{"approval_id":"...","decision":"approve"}` or `reject`. Duplicate/stale decisions
return 409. Pending jobs block another generation or deletion in that conversation.

SSE replays persisted `task_events` in sequence, then follows new actions. The UI
shows delegation, tool start/completion and rejection; it does not show private
model reasoning. Reloading restores pending approvals and the latest task's steps.
Postgres checkpoints preserve the point of interruption. Queued and waiting jobs
survive restart; interrupted running side effects are not automatically replayed.
Restart all server processes together when upgrading so old workers cannot claim
new approval-resume payloads. Migrations run automatically at startup.

Try: "Create hello.py that prints Bonjour, save it, then verify it." Without
selecting coding mode, the main agent should delegate, then an approval card should
appear before the write. After approval, a second card should precede code execution,
followed by the specialist report and the main agent's answer. Actual delegation
choices and code quality depend on the local models.

- 🔍 **Web search** via Tavily
- 📁 **File read/write** — the assistant can save and read files in `~/assistant_files/`
- ⚙️ **Code execution** — runs Python, JavaScript, or Bash snippets and reports output
- 🎬 **Video analysis** — upload a video; the agent describes it from extracted frames
- 🧠 **Long-term memory** — key/value facts stored in Postgres, injected into every prompt
- 💬 **Multi-turn conversations** — checkpointed in Postgres, switchable from the sidebar
- 🔐 **JWT auth** — register/login with HTTP-only cookies
- 📊 **LLM observability** — traces in Langfuse

---

## Project Structure

```
Root
|─── requirements.txt
|─── README.md
|─── src
     |─── .env.example
     |─── create_agents.py     # agent definitions
     |─── main.py              # FastAPI app
     |─── db
          |─── db.py           # Postgres pool + schema init
     |─── llm
          |─── context.py
          |─── graph.py        # LangGraph multi-agent graph
          |─── init_llm.py     # Ollama LLM wrapper
          |─── tools.py        # search, file, code tools
     |─── static
          |─── app.js
          |─── index.html
          |─── style.css
```

---

## Requirements

### Local inference on a 4 GB GPU / 16 GB RAM

The default main model is `qwen3.5:9b`, the summary model is `ministral-3:3b`, and
coding uses `qwen3.5:4b`. On a 4 GB GPU the 9b model can require CPU/RAM offloading
and run slowly. Model size alone does not guarantee
that the model and its KV cache fit entirely on the GPU.

Settings are documented in `src/.env.example`. Put overrides in `src/.env` or in
the server environment, then restart the server and its workers. Existing environment
values take precedence: replace an old `TASK_TIMEOUT_SECONDS=300` if present.

- `TASK_TIMEOUT_SECONDS=900`: total generation deadline, including all model calls,
  tools and conversation summarisation. Increasing it allows more time; it does not
  accelerate inference.
- `OLLAMA_HTTP_TIMEOUT_SECONDS=900`: Ollama HTTP timeout, defaulting to the task
  deadline when unset. The outer task deadline still limits the combined operation.
- `OLLAMA_NUM_CTX=8192`, `OLLAMA_NUM_PREDICT=1024`: text context and output limits.
  History budgeting reserves output space and overhead before each model call.
- `OLLAMA_VIDEO_NUM_CTX=32768`: separate context for image-heavy video input;
  video can require substantially more RAM and time than text.
- `OLLAMA_NUM_THREAD=6`, `OLLAMA_KEEP_ALIVE=5m`: CPU threads and model residency.
  Keeping a model loaded can reduce subsequent loading time.
- `SEARCH_MAX_RESULTS=3`, `SEARCH_RESULT_CHARS=1200`: reduce web content sent back
  to the model while retaining titles and source URLs. These limits trade detail
  for shorter prompts.

Code execution retains its separate subprocess timeout (at most 60 seconds).
Summarisation has its own configurable limits: `SUMMARY_NUM_PREDICT=512`,
`SUMMARY_TIMEOUT_SECONDS=240`, and `SUMMARY_SAVE_TIMEOUT_SECONDS=20`.
The prompt requests at most 200 words. A failure reports whether generation or
storage failed, and logs the traceback with the conversation ID. Failed summary
storage does not advance the history pruning boundary. All these operations still
count toward the overall task deadline. The database pool checks connections before
reuse, including after long model calls. If the browser stream closes before its
terminal event, the interface reports an interruption; reopen the conversation to
check the task rather than assuming it stopped on the server.
The UI currently waits for the complete durable response; it does not stream model
tokens. A long inference can therefore appear idle even while Ollama is working.

### Dependencies

- Python 3.12
- Postgres (e.g. [Neon](https://neon.tech) serverless, or local)
- Ollama running locally with these models pulled:
  - `ollama pull ministral-3:3b`
  - `ollama pull qwen3.5:9b`
  - `ollama pull qwen3.5:4b`
- Tavily API key
- Langfuse account (public + secret key)
- JWT secret: `openssl rand -hex 32`

### `.env` file (in `src/`)

```
DATABASE_URL=<your_postgres_connection_string>
JWT_SECRET=<your_jwt_secret>
TAVILY_API_KEY=<your_tavily_key>
LANGFUSE_PUBLIC_KEY=<your_langfuse_public_key>
LANGFUSE_SECRET_KEY=<your_langfuse_secret_key>

# Optional: where the assistant saves files (default: ~/assistant_files)
FILES_BASE_DIR=~/assistant_files
```

---

## Getting Started

```bash
# 1. Clone and set up venv
python -m venv venv
source venv/bin/activate       # Linux/macOS
# venv\Scripts\activate        # Windows

pip install -r requirements.txt

# 2. Copy and fill in .env
cp src/.env.example src/.env

# 3. Make sure Ollama is running
ollama serve   # if it doesn't start on boot

# 4. Start the server
cd src
python main.py
```

Open http://localhost:8000 in your browser.

---

## Health Check

`GET /health` — checks DB connectivity, Ollama reachability on port 11434, and Tavily key presence.

---

## Memory API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/memory` | List all memory entries |
| PUT | `/memory` | Upsert a `{ key, value }` entry |
| DELETE | `/memory/{key}` | Delete an entry |

Memory is automatically injected into every prompt so the assistant always knows what you've told it.

## Conversation Safety and Persistence

- Chat submission, history, status, rename and deletion all check conversation ownership.
- File tools and downloads use `FILES_BASE_DIR/<user_uuid>/`. The server supplies the
  user identity; it is not an argument the model can select.
- Assistant Markdown is filtered with DOMPurify, code examples are HTML-escaped,
  and scripts are restricted by a Content Security Policy. Marked, DOMPurify and
  JSZip are pinned and served from `src/static/vendor/`.
- Full human, assistant and tool messages are archived in `conversation_messages`.
  Visible assistant text produced before and after tool calls is combined into
  one answer per user turn, both for new responses and reloaded history. Successful
  file writes also display the generated file content in a fenced code block at
  the corresponding position. Other tool arguments, raw tool results and structured
  reasoning are not included in that answer.
  Model requests use a separate bounded view, keeping complete user/tool turns.
  The budget is estimated before every model call, including calls after tools.
  Oversized current turns are rejected with an actionable size-limit error.
- Requests are saved as `queued` jobs in Postgres. Each application process runs
  one worker, and a partial unique index prevents overlapping generations in the
  same conversation. Duplicate submissions return HTTP 409.
- Workers claim jobs atomically with `FOR UPDATE SKIP LOCKED`, renew a 60-second
  lease every 10 seconds, and enforce `TASK_TIMEOUT_SECONDS` (default 900).
  Queued jobs survive a restart. Interrupted running jobs are marked as errors
  after the lease expires, rather than automatically repeating tool side effects.
- SSE reads persisted job state, so reconnects can reach another application
  process. Deletion is refused while a job is queued, running or waiting for approval; completed
  conversation deletion also removes its archive, tasks, summaries and checkpoints.

### Existing installations

Schema additions run idempotently at application startup. Stop older application
processes before upgrading; their in-memory jobs cannot be recovered. Legacy jobs
without a lease are marked as interrupted. Surviving checkpoint messages are
copied into the archive on first read or the next chat turn. Messages already
removed by the previous summarisation implementation cannot be reconstructed.

Existing files directly inside `FILES_BASE_DIR` are deliberately not assigned to
an account automatically. After identifying their owner, move them into that
account's UUID directory. UUIDs are stored in `users.id`. Old download URLs still
use `/files/<relative_path>`; the authenticated account selects the namespace.

Videos now sample one frame per second over the first ten seconds, with a server
limit of 16 frames per request. Individual images are limited to 2 MB, and text
messages to 60,000 characters. Image-token costs are estimates; model-specific
limits can still require smaller inputs.

### Remaining execution limitation

`run_code_tool` still executes Python, Node or Bash directly on the host. Container
isolation has not been implemented. File namespaces protect the file APIs and
tools, but do not restrict arbitrary code execution. Do not expose this application
to untrusted users while host execution is enabled. A generation timeout cancels
the async task; it cannot forcibly stop synchronous code already running in a
worker thread. The code tool retains its own subprocess timeout of at most 60 seconds.

## Regression Tests

Python tests use the standard `unittest` runner and the existing application
dependencies. They replace external services and never require production keys:

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py" -v
```

DOM security tests use jsdom. SQL migration and queue tests execute the production
SQL against PGlite (PostgreSQL compiled to WASM), including a disk persistence
check across database close/reopen. Node.js is only required for these tests:

```powershell
npm ci --prefix tests
node --test tests/security.test.cjs tests/database.test.cjs
```

These tests do not replace a deployment check with the configured Postgres server,
Ollama models and Langfuse service.
