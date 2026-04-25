"""
Router do Telegram webhook.

Responsável por receber os eventos do Telegram e despachar
para o agente Sara.

Proteções contra duplicatas:
- update_id persistido no banco (sobrevive a restarts)
- Processamento em background: retorna 200 imediatamente, eliminando retries

Suporte a áudio:
- Mensagens de voz são transcritas via Whisper (Groq) antes de passar ao agente
"""

import logging
import tempfile

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from groq import Groq

import uuid

from app.agent.sara_agent import chat
from app.agent.session import set_session_state
from app.services.telegram import (
    enviar_mensagem_longa,
    enviar_inicio_planejamento,
    responder_callback,
    editar_revisao_tarefas,
    _revisao_state,
)
from app.config import ALLOWED_CHAT_ID, GROQ_API_KEY
from app.db.database import SessionLocal
from app.models.processed_update import ProcessedUpdate
from app.models.task import Task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["telegram"])

groq_client = Groq(api_key=GROQ_API_KEY)


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
    callback_query: dict | None = None


async def _transcrever_audio(file_id: str) -> str | None:
    """
    Baixa o arquivo de voz do Telegram e transcreve via Whisper (Groq).
    Retorna o texto transcrito ou None em caso de erro.
    """
    try:
        from app.services.telegram import bot
        telegram_file = await bot.get_file(file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await telegram_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as audio_file:
            transcricao = groq_client.audio.transcriptions.create(
                file=("audio.ogg", audio_file, "audio/ogg"),
                model="whisper-large-v3-turbo",
                language="pt",
            )

        import os
        os.unlink(tmp_path)

        texto = transcricao.text.strip()
        logger.info(f"[Whisper] Transcrição: {texto[:100]}")
        return texto

    except Exception as e:
        logger.error(f"[Whisper] Erro na transcrição: {e}", exc_info=True)
        return None


def _marcar_tarefa_done(task_id: str) -> bool:
    """Marca uma tarefa como concluída pelo seu UUID."""
    db = SessionLocal()
    try:
        from datetime import datetime as _dt
        tarefa = db.query(Task).filter(Task.id == uuid.UUID(task_id)).first()
        if tarefa:
            tarefa.status = "done"
            tarefa.updated_at = _dt.utcnow()
            db.commit()
            logger.info(f"[Callback] Tarefa {task_id[:8]}... marcada como concluída")
            return True
        return False
    except Exception as e:
        db.rollback()
        logger.error(f"[Callback] Erro ao marcar tarefa {task_id}: {e}")
        return False
    finally:
        db.close()


async def _processar_callback(data: str, chat_id: str, message_id: int, query_id: str) -> None:
    """Processa o toque em um botão do inline keyboard de revisão de tarefas."""
    await responder_callback(query_id)

    if data.startswith("task:"):
        task_id = data.split(":", 1)[1]
        _marcar_tarefa_done(task_id)
        state = _revisao_state.get(chat_id)
        if state and task_id in state["tasks"]:
            state["tasks"][task_id]["done"] = True
        await editar_revisao_tarefas(chat_id, message_id)

    elif data == "concluir_revisao":
        set_session_state(chat_id, "planning")
        await enviar_inicio_planejamento(chat_id)


async def _processar_mensagem(chat_id: str, text: str, first_name: str) -> None:
    """Processa a mensagem em background após retornar 200 ao Telegram."""
    try:
        from app.agent.sara_agent import _quer_iniciar_planejamento, limpar_historico_planning
        from app.scheduler.jobs import iniciar_planejamento_manual

        if _quer_iniciar_planejamento(text):
            limpar_historico_planning(chat_id)
            await iniciar_planejamento_manual(chat_id)
            return

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

        # Inline keyboard callback (botões da revisão de tarefas)
        if update.callback_query:
            cq = update.callback_query
            cq_chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
            if ALLOWED_CHAT_ID and cq_chat_id != ALLOWED_CHAT_ID:
                logger.warning(f"[Webhook] Callback não autorizado de {cq_chat_id}")
                return JSONResponse(status_code=200, content={"status": "unauthorized"})
            background_tasks.add_task(
                _processar_callback,
                cq.get("data", ""),
                cq_chat_id,
                cq.get("message", {}).get("message_id", 0),
                cq.get("id", ""),
            )
            return JSONResponse(status_code=200, content={"status": "ok"})

        message = update.message
        if not message:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        chat_id = str(message["chat"]["id"])
        first_name = message["chat"].get("first_name", "Usuário")

        # Acesso restrito ao dono do bot
        if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
            logger.warning(f"[Webhook] Acesso negado para {first_name} ({chat_id})")
            return JSONResponse(status_code=200, content={"status": "unauthorized"})

        text = message.get("text", "")

        # Mensagem de voz — transcrever via Whisper antes de processar
        if not text and "voice" in message:
            file_id = message["voice"]["file_id"]
            logger.info(f"🎤 Áudio de {first_name} ({chat_id}), transcrevendo...")
            text = await _transcrever_audio(file_id)
            if not text:
                await enviar_mensagem_longa(chat_id, "Não consegui entender o áudio. Pode repetir ou digitar?")
                return JSONResponse(status_code=200, content={"status": "transcription_failed"})
            logger.info(f"🎤 Transcrito: {text[:50]}...")

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
