# Personal-AI-Assistant

An ongoing personal project to build a local multi-agent AI assistant. Runs entirely on your machine via Ollama.

---

## Agents

| Agent | Model | Trigger | Tools |
|---|---|---|---|
| **main_agent** | Ministral 3 14b | Default for all text messages | Web search, read/write files, run code |
| **coding_agent** | Ministral 3 8b | 💻 button in the UI | Read/write files, run code (write → run → fix loop) |
| **video_agent** | Ministral 3 3b | Video file upload | None (vision only) |

---

## Features

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

- Python 3.12
- Postgres (e.g. [Neon](https://neon.tech) serverless, or local)
- Ollama running locally with these models pulled:
  - `ollama pull ministral-3:3b`
  - `ollama pull ministral-3:14b`
  - `ollama pull codellama:7b`
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
