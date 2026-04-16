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

import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent.sara_agent import chat
from app.services.telegram import enviar_mensagem_longa
from app.config import ALLOWED_CHAT_ID
from app.db.database import SessionLocal
from app.models.processed_update import ProcessedUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["telegram"])


def _ja_processado(update_id: int) -> bool:
    """Verifica se o update_id já foi processado (persistido no banco)."""
    db = SessionLocal()
    try:
        return db.query(ProcessedUpdate).filter(
            ProcessedUpdate.update_id == update_id
        ).first() is not None
    finally:
        db.close()


def _marcar_processado(update_id: int) -> None:
    """Persiste o update_id no banco para deduplicação entre restarts."""
    db = SessionLocal()
    try:
        db.add(ProcessedUpdate(update_id=update_id))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


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
        # Deduplicação por update_id — persistida no banco, sobrevive a restarts
        if update.update_id is not None:
            if _ja_processado(update.update_id):
                logger.info(f"[Webhook] update_id {update.update_id} já processado, ignorando.")
                return JSONResponse(status_code=200, content={"status": "duplicate"})
            _marcar_processado(update.update_id)

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
