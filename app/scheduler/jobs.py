"""
Jobs do APScheduler para envio de lembretes e briefing diário.

Jobs:
- verificar_lembretes: roda a cada 1 minuto
- briefing_diario: roda diariamente no horário configurado
"""

import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.database import SessionLocal
from app.models.reminder import Reminder
from app.models.task import Task
from app.services.telegram import enviar_lembrete, enviar_briefing
from app.config import BRIEFING_HORA

logger = logging.getLogger(__name__)

TIMEZONE = pytz.timezone("America/Sao_Paulo")


async def verificar_lembretes():
    """
    Busca lembretes pendentes no banco e envia via Telegram.
    Marca como enviados após o envio bem-sucedido.
    """
    logger.info("[Scheduler] Verificando lembretes pendentes...")

    db = SessionLocal()
    try:
        agora = datetime.now(TIMEZONE)

        lembretes = (
            db.query(Reminder)
            .filter(
                Reminder.sent == False,
                Reminder.remind_at <= agora,
            )
            .all()
        )

        if not lembretes:
            return

        logger.info(f"[Scheduler] {len(lembretes)} lembrete(s) para enviar")

        for lembrete in lembretes:
            enviado = await enviar_lembrete(
                str(lembrete.user_id), lembrete.message
            )

            if enviado:
                lembrete.sent = True
                db.commit()
                logger.info(
                    f"[Scheduler] Lembrete enviado para {lembrete.user_id}"
                )
            else:
                logger.warning(
                    f"[Scheduler] Falha ao enviar lembrete para {lembrete.user_id}"
                )

    except Exception as e:
        db.rollback()
        logger.error(f"[Scheduler] Erro ao verificar lembretes: {e}")
    finally:
        db.close()


async def briefing_diario():
    """
    Envia o briefing diário com as tarefas do dia para cada usuário.
    """
    logger.info("[Scheduler] Enviando briefing diário...")

    db = SessionLocal()
    try:
        agora = datetime.now(TIMEZONE)
        inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        fim_dia = agora.replace(hour=23, minute=59, second=59, microsecond=0)

        tarefas_por_usuario = (
            db.query(Task)
            .filter(
                Task.status == "pending",
                Task.due_date >= inicio_dia,
                Task.due_date <= fim_dia,
            )
            .all()
        )

        if not tarefas_por_usuario:
            logger.info("[Scheduler] Sem tarefas para hoje, briefing pulado")
            return

        usuarios = {}
        for tarefa in tarefas_por_usuario:
            if tarefa.user_id not in usuarios:
                usuarios[tarefa.user_id] = []
            horario = (
                tarefa.due_date.strftime("%H:%M")
                if tarefa.due_date
                else "sem horário"
            )
            usuarios[tarefa.user_id].append(f"{horario} — {tarefa.title}")

        for user_id, tarefas in usuarios.items():
            enviado = await enviar_briefing(user_id, tarefas)
            if enviado:
                logger.info(f"[Scheduler] Briefing enviado para {user_id}")
            else:
                logger.warning(
                    f"[Scheduler] Falha ao enviar briefing para {user_id}"
                )

    except Exception as e:
        logger.error(f"[Scheduler] Erro ao enviar briefing: {e}")
    finally:
        db.close()


def iniciar_scheduler(scheduler: AsyncIOScheduler):
    """
    Configura e inicia os jobs do scheduler.

    Args:
        scheduler: Instância do AsyncIOScheduler.
    """
    scheduler.add_job(
        verificar_lembretes,
        "interval",
        minutes=1,
        id="verificar_lembretes",
        name="Verifica e envia lembretes pendentes",
        replace_existing=True,
    )

    hora, minuto = map(int, BRIEFING_HORA.split(":"))
    scheduler.add_job(
        briefing_diario,
        "cron",
        hour=hora,
        minute=minuto,
        id="briefing_diario",
        name="Envia briefing diário de tarefas",
        replace_existing=True,
        timezone=TIMEZONE,
    )

    logger.info(
        f"[Scheduler] Jobs configurados: lembretes a cada 1min, "
        f"briefing às {BRIEFING_HORA}"
    )
