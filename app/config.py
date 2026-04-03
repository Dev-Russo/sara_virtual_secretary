"""
Configurações centrais da aplicação.
Todas as variáveis de ambiente são lidas aqui e exportadas
para o resto do projeto — nunca use os.getenv() fora deste arquivo.
"""

from dotenv import load_dotenv
import os

# Carrega o arquivo .env na raiz do projeto
load_dotenv()

# --- Modelo de linguagem ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.3"))
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "1024"))

# --- Banco de dados ---
DATABASE_URL = os.getenv("DATABASE_URL")

# --- Usuário ---
# No MVP o usuário é fixo. Na Fase 2 virá do número de telefone do WhatsApp.
USER_ID = os.getenv("USER_ID", "5511999999999")

# --- Configurações do agente ---
TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")

# Quantas mensagens anteriores carregar para o contexto do agente.
# Mais mensagens = mais contexto, mas também mais tokens gastos por chamada.
HISTORICO_LIMITE = int(os.getenv("HISTORICO_LIMITE", "10"))