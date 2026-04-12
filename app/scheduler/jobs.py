"""
Jobs do APScheduler para envio de lembretes e briefing diário.

Jobs:
- verificar_lembretes: roda a cada 1 minuto
- briefing_diario: roda diariamente no horário configurado

Correções aplicadas:
- #3A: comparações de datetime agora usam timezone-aware em ambos os lados
- #2A: briefing inclui tarefas SEM due_date (NULL)
- #2B: catchup no startup — se perdeu o horário, envia ao iniciar
- #2C: briefing SEMPRE envia algo, mesmo sem tarefas
"""

import logging
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func

from app.db.database import SessionLocal
from app.models.reminder import Reminder
from app.models.task import Task
from app.models.conversation import ConversationHistory
from app.services.telegram import enviar_lembrete, enviar_briefing
from app.config import BRIEFING_HORA

logger = logging.getLogger(__name__)

TIMEZONE = pytz.timezone("America/Sao_Paulo")

# Flag para controlar se o catchup do briefing já rodou
briefing_catchup_done = False


async def verificar_lembretes():
    """
    Busca lembretes pendentes no banco e envia via Telegram.
    Marca como enviados após o envio bem-sucedido.

    #3A — Agora compara datetime aware com aware:
    - Reminder.remind_at é armazenado como timezone-aware (desde a correção em tools.py)
    - datetime.now(TIMEZONE) também é aware
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
            # Garante que remind_at é aware para comparação/logs
            remind_dt = lembrete.remind_at
            if remind_dt and remind_dt.tzinfo is None:
                remind_dt = TIMEZONE.localize(remind_dt)

            enviado = await enviar_lembrete(
                str(lembrete.user_id), lembrete.message
            )

            if enviado:
                lembrete.sent = True
                db.commit()
                logger.info(
                    f"[Scheduler] Lembrete enviado para {lembrete.user_id} "
                    f"(agendado: {remind_dt.strftime('%d/%m %H:%M')})"
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


async def briefing_diario(forçar_envio: bool = False):
    """
    Envia o briefing diário com as tarefas do dia para cada usuário.

    #2A — Agora inclui tarefas SEM due_date (pending sem prazo)
    #2C — Se forçar_envio=True, envia mesmo sem tarefas ("dia livre")

    Args:
        forçar_envio: Se True, envia mensagem mesmo sem tarefas.
    """
    logger.info("[Scheduler] Enviando briefing diário...")

    db = SessionLocal()
    try:
        agora = datetime.now(TIMEZONE)
        inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        fim_dia = agora.replace(hour=23, minute=59, second=59, microsecond=0)

        # Garante que os datetimes são aware
        if inicio_dia.tzinfo is None:
            inicio_dia = TIMEZONE.localize(inicio_dia.replace(tzinfo=None))
            fim_dia = TIMEZONE.localize(fim_dia.replace(tzinfo=None))

        # #2A — Query 1: tarefas COM data para hoje
        tarefas_com_data = (
            db.query(Task)
            .filter(
                Task.status == "pending",
                Task.due_date >= inicio_dia,
                Task.due_date <= fim_dia,
            )
            .all()
        )

        # #2A — Query 2: tarefas SEM data (due_date IS NULL)
        tarefas_sem_data = (
            db.query(Task)
            .filter(
                Task.status == "pending",
                Task.due_date.is_(None),
            )
            .all()
        )

        todas_tarefas = tarefas_com_data + tarefas_sem_data

        if not todas_tarefas:
            if forçar_envio:
                # #2C — Envia mensagem de "dia livre"
                logger.info("[Scheduler] Sem tarefas, enviando 'dia livre'")
                # Precisamos descobrir usuários ativos para enviar
                # (busca últimos usuários que interagiram)
                await _enviar_briefing_vazio()
            else:
                logger.info("[Scheduler] Sem tarefas para hoje, briefing pulado")
            return

        # Agrupa por usuário
        usuarios: dict[str, list[str]] = {}
        for tarefa in todas_tarefas:
            if tarefa.user_id not in usuarios:
                usuarios[tarefa.user_id] = []
            if tarefa.due_date:
                dt = tarefa.due_date
                if dt.tzinfo is None:
                    dt = TIMEZONE.localize(dt)
                horario = dt.strftime("%H:%M")
            else:
                horario = "sem prazo"
            usuarios[tarefa.user_id].append(f"{horario} — {tarefa.title}")

        for user_id, tarefas in usuarios.items():
            enviado = await enviar_briefing(user_id, tarefas)
            if enviado:
                logger.info(f"[Scheduler] Briefing enviado para {user_id} ({len(tarefas)} tarefas)")
            else:
                logger.warning(
                    f"[Scheduler] Falha ao enviar briefing para {user_id}"
                )

    except Exception as e:
        logger.error(f"[Scheduler] Erro ao enviar briefing: {e}")
    finally:
        db.close()


async def _enviar_briefing_vazio():
    """
    #2C — Envia mensagem de "dia livre" para usuários ativos recentes.
    Busca os últimos usuários que tiveram conversa no histórico.
    """
    db = SessionLocal()
    try:
        # Busca usuários ativos nas últimas 48h
        limite = datetime.now(TIMEZONE) - timedelta(hours=48)

        usuarios_ativos = (
            db.query(ConversationHistory.user_id)
            .filter(ConversationHistory.created_at >= limite)
            .distinct()
            .all()
        )

        for (user_id,) in usuarios_ativos:
            enviado = await enviar_briefing(user_id, [])
            if enviado:
                logger.info(f"[Scheduler] Briefing 'dia livre' enviado para {user_id}")

    except Exception as e:
        logger.error(f"[Scheduler] Erro ao enviar briefing vazio: {e}")
    finally:
        db.close()


async def briefing_catchup():
    """
    #2B — Verifica se o briefing de hoje já foi enviado.
    Se o horário já passou e não foi enviado, dispara agora.
    Chamado UMA VEZ no startup do scheduler.
    """
    global briefing_catchup_done
    if briefing_catchup_done:
        return
    briefing_catchup_done = True

    agora = datetime.now(TIMEZONE)
    hora_briefing, min_briefing = map(int, BRIEFING_HORA.split(":"))
    horario_briefing = agora.replace(
        hour=hora_briefing, minute=min_briefing, second=0, microsecond=0
    )

    # Se já passou do horário do briefing hoje, verifica se precisamos fazer catchup
    if agora >= horario_briefing:
        logger.info(
            f"[Scheduler-Catchup] Horário do briefing já passou ({BRIEFING_HORA}). "
            f"Verificando se precisa enviar..."
        )
        # Envia o briefing agora (catchup)
        await briefing_diario(forçar_envio=True)
    else:
        logger.info(
            f"[Scheduler-Catchup] Briefing agendado para {BRIEFING_HORA}. "
            f"Faltam {(horario_briefing - agora).total_seconds() / 60:.0f}min."
        )


def iniciar_scheduler(scheduler: AsyncIOScheduler):
    """
    Configura e inicia os jobs do scheduler.

    #2B — Adiciona job de catchup que roda 30 segundos após o start.
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
        kwargs={"forçar_envio": True},
    )

    # #2B — Catchup: roda 30 segundos após o start para verificar
    # se o briefing de hoje já foi enviado
    scheduler.add_job(
        briefing_catchup,
        "date",
        run_date=datetime.now(TIMEZONE) + timedelta(seconds=30),
        id="briefing_catchup",
        name="Verifica se briefing de hoje precisa ser enviado (catchup)",
        replace_existing=True,
        timezone=TIMEZONE,
    )

    logger.info(
        f"[Scheduler] Jobs configurados: lembretes a cada 1min, "
        f"briefing às {BRIEFING_HORA} (com catchup)"
    )
