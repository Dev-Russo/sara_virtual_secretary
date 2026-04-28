# Sara — Virtual Secretary

[![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://www.python.org/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> Your AI-powered personal assistant via Telegram, built with FastAPI, Groq (Llama 3.3), and PostgreSQL.

---

## Features

- **Natural Language Processing** — Understand tasks, reminders, and questions without rigid commands
- **Telegram Integration** — Full bot support via webhook + ngrok for local development
- **Smart Scheduler** — Automatic reminder delivery and daily briefings via APScheduler
- **Persistent Memory** — Conversation history stored in PostgreSQL for context-aware responses
- **Tool Calling** — Groq LLM decides when to save tasks, list them, mark as done, or create reminders
- **Multi-user Support** — Isolated data per Telegram `chat_id`
- **CLI Mode** — Terminal interface for debugging and development

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Framework** | FastAPI + Uvicorn |
| **LLM** | Groq (llama-3.3-70b-versatile) |
| **Database** | PostgreSQL 16 + SQLAlchemy 2.0 |
| **Migrations** | Alembic |
| **Scheduler** | APScheduler (async) |
| **Bot Platform** | Telegram (python-telegram-bot) |
| **Dev Tunneling** | ngrok (webhook exposure) |
| **Containerization** | Docker Compose |

---

## Architecture

```
┌─────────────────┐
│  User (Telegram)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Telegram API   │
└────────┬────────┘
         │  webhook POST
         ▼
┌─────────────────┐      ┌──────────────────┐
│   ngrok (dev)   │─────▶│  FastAPI Server  │
└─────────────────┘      │  /webhook/telegram│
                         └────────┬─────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │  Sara Agent      │
                         │  (Groq LLM)      │
                         └──┬───────────┬───┘
                            │           │
                     Tool   │           │  Response
                            ▼           ▼
                         ┌──────┐  ┌────────────┐
                         │ DB   │  │ Telegram   │
                         │(PG16)│  │ Bot API    │
                         └──────┘  └────────────┘

            ┌──────────────────────────────┐
            │       APScheduler            │
            │  • Reminders (every 1 min)   │
            │  • Daily briefing (08:00)    │
            └──────────────────────────────┘
```

---

## Project Structure

```
sara-virtual-secretary/
├── app/
│   ├── agent/
│   │   ├── prompts.py          # System prompt for Sara's personality
│   │   ├── sara_agent.py       # Main agent loop (Groq + tool calling)
│   │   └── tools.py            # Tool functions (save_task, create_reminder, etc.)
│   ├── api/
│   │   └── routes/
│   │       ├── telegram.py     # Webhook endpoint (/webhook/telegram)
│   │       └── health.py       # Health check (/health)
│   ├── db/
│   │   └── database.py         # SQLAlchemy engine, session, base
│   ├── models/
│   │   ├── conversation.py     # Conversation history model
│   │   ├── reminder.py         # Reminder model
│   │   └── task.py             # Task model
│   ├── scheduler/
│   │   └── jobs.py             # APScheduler jobs (reminders + briefing)
│   ├── services/
│   │   └── telegram.py         # Telegram Bot API wrapper
│   ├── schemas/                # Pydantic schemas
│   ├── config.py               # Centralized environment config
│   └── main.py                 # FastAPI application entrypoint
├── alembic/                    # Database migrations
├── fases/                      # Development phase documentation
│   ├── FASE_1_CORE_CLI.md
│   ├── FASE_2_TELEGRAM_SCHEDULER.md
│   ├── FASE_3_DEPLOY.md
│   ├── FASE_4_DASHBOARD_CALENDAR.md
│   └── FASE_5_RAG.md
├── cli.py                      # CLI interface for debugging
├── docker-compose.yaml         # PostgreSQL service
├── requirements.txt
└── .env.example                # Environment variable template
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- [Groq API key](https://console.groq.com/)
- [Telegram Bot Token](https://core.telegram.org/bots#how-do-i-create-a-bot)
- [ngrok](https://ngrok.com/) (for local webhook development)

### 1. Clone & Setup

```bash
git clone <repository-url>
cd sara-virtual-secretary

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Setup environment variables
cp .env.example .env
# Edit .env and fill in your credentials
```

### Local CLI-first workflow

For local development, keep production values on the server and use a separate env file here:

```bash
cp .env.local.example .env.local
```

Use the same API keys if you want, but point `DATABASE_URL` to your local Postgres. When running locally, export:

```bash
export ENV_FILE=.env.local
```

### 2. Database

```bash
# Start PostgreSQL
docker-compose up -d

# Run migrations
alembic upgrade head
```

With the local env file:

```bash
export ENV_FILE=.env.local
alembic upgrade head
```

### 3. Development (Webhook + ngrok)

```bash
# Terminal 1: Start ngrok
ngrok http 8000

# Copy the ngrok URL (e.g., https://abc123.ngrok-free.app)
# Add it to .env: WEBHOOK_URL=https://abc123.ngrok-free.app
# Also set a random secret in .env: TELEGRAM_WEBHOOK_SECRET=change-me
# Example:
# export TELEGRAM_WEBHOOK_SECRET="$(openssl rand -hex 32)"

# Terminal 2: Register webhook with Telegram
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://abc123.ngrok-free.app/webhook/telegram", "secret_token": "'"${TELEGRAM_WEBHOOK_SECRET}"'"}'

# Terminal 3: Start the server
uvicorn app.main:app --reload
```

Now open your Telegram bot and send a message!

### 4. CLI Mode (Debug)

```bash
export ENV_FILE=.env.local
python cli.py
```

This is the safest local path for now: same agent code, same LLM credentials if desired, but no local webhook and no shared production database.

---

## Agent Tools

The Sara agent has access to the following tools, automatically invoked by the LLM when needed:

| Tool | Description | Database Action |
|---|---|---|
| `save_task` | Save a new task | `INSERT INTO tasks` |
| `create_reminder` | Schedule a reminder | `INSERT INTO reminders` |
| `list_tasks` | List pending tasks | `SELECT FROM tasks` |
| `complete_task` | Mark task as done | `UPDATE tasks SET status='done'` |

---

## Scheduler Jobs

| Job | Frequency | Description |
|---|---|---|
| `verificar_lembretes` | Every 1 minute | Checks and sends pending reminders |
| `briefing_diario` | Daily (configurable, default 08:00) | Sends daily task summary |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/telegram` | Receives Telegram webhook events |
| `GET` | `/health` | Health check |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token from BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Yes (webhook) | — | Shared secret validated from `X-Telegram-Bot-Api-Secret-Token` |
| `GROQ_API_KEY` | Yes | — | Groq API key |
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | LLM model to use |
| `GROQ_TEMPERATURE` | No | `0.3` | Model creativity (0.0–1.0) |
| `GROQ_MAX_TOKENS` | No | `1024` | Max tokens per response |
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `WEBHOOK_URL` | Yes (prod) | — | Public URL for Telegram webhook |
| `BRIEFING_HORA` | No | `08:00` | Daily briefing time |
| `TIMEZONE` | No | `America/Sao_Paulo` | Timezone for scheduling |
| `HISTORICO_LIMITE` | No | `10` | Max conversation history messages |

---

## Development Phases

This project is being developed iteratively:

- **Phase 1:** Core CLI — Conversational agent with task/reminder management ✅
- **Phase 2:** Telegram Integration + Scheduler — Webhook, bot API, automatic reminders ✅
- **Phase 3:** Deploy — Production deployment to VPS, HTTPS, 24/7 availability 🚧
- **Phase 4:** Dashboard & Calendar — Web interface and Google Calendar integration *(planned)*
- **Phase 5:** RAG & Long-term Memory — Semantic memory with pgvector *(planned)*

See the `fases/` directory for detailed specifications.

---

## How It Works

### Message Processing Flow

1. User sends message to Telegram bot
2. Telegram forwards to FastAPI via webhook (`POST /webhook/telegram`)
3. Agent loads conversation history from database
4. LLM decides: respond directly or use a tool (save task, list tasks, etc.)
5. If tool is used: execute, then generate response with results
6. Save conversation to database
7. Send response back to Telegram user

### Agent Decision Cycle

```
User message
    ↓
Load history (last N messages)
    ↓
Build context: [system prompt + history + current message]
    ↓
First Groq call (tool_choice=auto)
    ↓
┌─────────────────────────────┐
│ Model wants to use tools?   │
└─────────────────────────────┘
     Yes ↓            No ↓
Execute tool(s)    Direct response
in database
     ↓
Second Groq call
with tool results
     ↓
Final response
     ↓
Save [user + assistant] to history
```

---

## Database Schema

### `tasks`
Stores user tasks with optional due dates and priorities.

### `reminders`
Stores scheduled reminders with delivery time tracking.

### `conversation_history`
Stores conversation context for multi-turn understanding.

---

## Troubleshooting

### Webhook not receiving messages
- Verify ngrok is running and URL matches `WEBHOOK_URL` in `.env`
- Verify `TELEGRAM_WEBHOOK_SECRET` is set in the app and registered in Telegram
- Re-register webhook: `curl -X POST "https://api.telegram.org/bot${TOKEN}/setWebhook" -H "Content-Type: application/json" -d '{"url": "<ngrok-url>/webhook/telegram", "secret_token": "'"${TELEGRAM_WEBHOOK_SECRET}"'"}'`
- Check FastAPI logs for incoming requests

### Scheduler not sending reminders
- Ensure APScheduler started (check logs for "🚀 Scheduler iniciado")
- Verify `remind_at` is in the past and `sent=false` in database
- Check Telegram bot token is correct

### Database connection errors
- Ensure Docker container is running: `docker-compose ps`
- Verify `DATABASE_URL` matches container credentials
- Run `alembic upgrade head` if tables are missing

---

## Contributing

This is a personal project, but feel free to fork, open issues, or suggest improvements!

---

## License

MIT

---

## Acknowledgments

- [Groq](https://groq.com/) for fast, free LLM inference
- [FastAPI](https://fastapi.tiangolo.com/) for the excellent async framework
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) for the Telegram integration
- [LangGraph](https://langchain-ai.github.io/langgraph/) for agent architecture inspiration

---

**Made with ❤️ by Murilo Russo**
