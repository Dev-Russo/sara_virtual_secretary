"""
Configurações centrais da aplicação.
Todas as variáveis de ambiente são lidas aqui e exportadas
para o resto do projeto — nunca use os.getenv() fora deste arquivo.
"""

from dotenv import load_dotenv
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Permite alternar entre .env e .env.local sem mexer no código.
env_file_name = os.getenv("ENV_FILE", ".env")
ENV_PATH = Path(env_file_name)
if not ENV_PATH.is_absolute():
    ENV_PATH = PROJECT_ROOT / ENV_PATH
load_dotenv(dotenv_path=ENV_PATH)

# --- Groq (mantido para transcrição de áudio via Whisper) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# --- Anthropic (LLM principal) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "1024"))

# --- Banco de dados ---
DATABASE_URL = os.getenv("DATABASE_URL")

# --- Usuário (Fase 1 — mantido para compatibilidade com CLI) ---
USER_ID = os.getenv("USER_ID", "5511999999999")

# --- Configurações do agente ---
TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
HISTORICO_LIMITE = int(os.getenv("HISTORICO_LIMITE", "10"))

# --- Telegram (Fase 2) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BRIEFING_HORA = os.getenv("BRIEFING_HORA", "08:00")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "")
CHECKIN_HORA = os.getenv("CHECKIN_HORA", "21:00")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
