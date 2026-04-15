"""
Configurações centrais da aplicação.
Todas as variáveis de ambiente são lidas aqui e exportadas
para o resto do projeto — nunca use os.getenv() fora deste arquivo.
"""

from dotenv import load_dotenv
import os
from pathlib import Path

# Força o carregamento do .env do diretório raiz do projeto
ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# --- Modelo de linguagem ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.3"))
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "1024"))

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