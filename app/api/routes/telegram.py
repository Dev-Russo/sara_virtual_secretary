"""
Router do Telegram webhook.

Responsável por receber os eventos do Telegram e despachar
para o agente Sara.

Proteções contra duplicatas:
- _processed_updates: cache em memória dos update_ids já processados
  (evita reprocessamento quando o Telegram reenvia por timeout)
- Processamento em background: retorna 200 imediatamente antes de chamar
  o agente, eliminando a causa raiz dos retries
"""

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent.sara_agent import chat
from app.services.telegram import enviar_mensagem_longa
from app.config import ALLOWED_CHAT_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["telegram"])

# Cache de update_ids já processados (últimos 1000)
_processed_updates: set[int] = set()
_MAX_CACHE = 1000


class TelegramUpdate(BaseModel):
    """Payload recebido do Telegram."""
    update_id: int | None = None
    message: dict | None = None
    edited_message: dict | None = None


async def _processar_mensagem(chat_id: str, text: str, first_name: str) -> None:
    """Processa a mensagem em background após retornar 200 ao Telegram."""
    try:
        resposta = chat(text, user_id=chat_id)
        enviado = await enviar_mensagem_longa(chat_id, resposta)
        if not enviado:
            logger.warning(f"❌ Falha ao enviar resposta para {first_name} ({chat_id})")
    except Exception as e:
        logger.error(f"Erro ao processar mensagem de {chat_id}: {e}", exc_info=True)


@router.post("/telegram")
async def telegram_webhook(update: TelegramUpdate, background_tasks: BackgroundTasks):
    """
    Recebe eventos do Telegram e despacha para o agente.

    Retorna HTTP 200 imediatamente e processa em background para evitar
    que o Telegram considere timeout e reenvie o mesmo update.
    """
    try:
        # Deduplicação por update_id — ignora retries do Telegram
        if update.update_id is not None:
            if update.update_id in _processed_updates:
                logger.info(f"[Webhook] update_id {update.update_id} já processado, ignorando.")
                return JSONResponse(status_code=200, content={"status": "duplicate"})
            _processed_updates.add(update.update_id)
            # Limpa cache se crescer demais
            if len(_processed_updates) > _MAX_CACHE:
                oldest = sorted(_processed_updates)[:_MAX_CACHE // 2]
                for uid in oldest:
                    _processed_updates.discard(uid)

        message = update.message
        if not message:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        chat_id = str(message["chat"]["id"])
        text = message.get("text", "")
        first_name = message["chat"].get("first_name", "Usuário")

        # Acesso restrito ao dono do bot
        if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
            logger.warning(f"[Webhook] Acesso negado para {first_name} ({chat_id})")
            return JSONResponse(status_code=200, content={"status": "unauthorized"})

        if not text:
            logger.info(f"Mensagem não-texto recebida de {first_name} ({chat_id})")
            return JSONResponse(status_code=200, content={"status": "ignored"})

        logger.info(f"📨 Mensagem de {first_name} ({chat_id}): {text[:50]}...")

        # Processa em background — retorna 200 antes de chamar o agente
        background_tasks.add_task(_processar_mensagem, chat_id, text, first_name)

        return JSONResponse(status_code=200, content={"status": "ok"})

    except Exception as e:
        logger.error(f"Erro no webhook: {e}", exc_info=True)
        return JSONResponse(status_code=200, content={"status": "error"})
