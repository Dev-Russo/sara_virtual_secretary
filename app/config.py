"""
Configurações centrais da aplicação.
Todas as variáveis de ambiente são lidas aqui e exportadas
para o resto do projeto — nunca use os.getenv() fora deste arquivo.
"""

from dotenv import load_dotenv
import os

load_dotenv()

# --- Modelo de linguagem ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.3"))
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "1024"))

# --- Banco de dados ---
DATABASE_URL = os.getenv("DATABASE_URL")

# --- Usuário ---
# No MVP o usuário é fixo — na fase do Telegram virá do chat_id
USER_ID = os.getenv("USER_ID")
if not USER_ID:
    raise ValueError(
        "USER_ID não está configurado no arquivo .env. "
        "Para o MVP, defina um identificador fixo (ex: USER_ID=user001)"
    )

# --- Configurações do agente ---
TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
HISTORICO_LIMITE = int(os.getenv("HISTORICO_LIMITE", "10"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")