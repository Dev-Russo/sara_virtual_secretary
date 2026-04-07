"""
Router do Telegram webhook.

Responsável por receber os eventos do Telegram e despachar
para o agente Sara.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent.sara_agent import chat
from app.services.telegram import enviar_mensagem_longa

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["telegram"])


class TelegramUpdate(BaseModel):
    """Payload recebido do Telegram."""
    update_id: int | None = None
    message: dict | None = None
    edited_message: dict | None = None


@router.post("/telegram")
async def telegram_webhook(update: TelegramUpdate):
    """
    Recebe eventos do Telegram e despacha para o agente.

    O Telegram envia um POST para este endpoint a cada nova mensagem.
    Retornamos HTTP 200 imediatamente (requisito do Telegram) e
    processamos a mensagem de forma assíncrona.
    """
    try:
        # Ignora mensagens que não sejam texto
        message = update.message
        if not message:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        # Extrai informações da mensagem
        chat_id = str(message["chat"]["id"])
        text = message.get("text", "")
        first_name = message["chat"].get("first_name", "Usuário")

        # Ignora mensagens sem texto (ex: imagens, áudios, stickers)
        if not text:
            logger.info(
                f"Mensagem não-texto recebida de {first_name} ({chat_id})"
            )
            return JSONResponse(status_code=200, content={"status": "ignored"})

        logger.info(
            f"📨 Mensagem de {first_name} ({chat_id}): {text[:50]}..."
        )

        # Processa a mensagem através do agente Sara
        resposta = chat(text, user_id=chat_id)

        # Envia a resposta de volta ao usuário no Telegram
        enviado = await enviar_mensagem_longa(chat_id, resposta)

        if not enviado:
            logger.warning(
                f"❌ Falha ao enviar resposta para {first_name} ({chat_id})"
            )

        return JSONResponse(status_code=200, content={"status": "ok"})

    except Exception as e:
        logger.error(f"Erro no webhook: {e}", exc_info=True)
        # Retorna 200 mesmo em caso de erro para evitar
        # reprocessamento pelo Telegram
        return JSONResponse(status_code=200, content={"status": "error"})
