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
from app.models.processed_update import ProcessedUpdate
from app.services.telegram import (
    enviar_lembrete,
    enviar_briefing,
    enviar_inicio_planejamento,
    enviar_revisao_tarefas,
)
from app.agent.session import set_session_state
from app.agent.sara_agent import limpar_historico_planning
from app.models.tool_call_log import ToolCallLog
from app.config import BRIEFING_HORA, CHECKIN_HORA, ALLOWED_CHAT_ID

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

        # Agrupa por usuário — tarefas com horário primeiro (ordenadas), sem prazo no final
        usuarios: dict[str, list[tuple]] = {}
        for tarefa in todas_tarefas:
            if tarefa.user_id not in usuarios:
                usuarios[tarefa.user_id] = []
            if tarefa.due_date:
                dt = tarefa.due_date
                if dt.tzinfo is None:
                    dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
                else:
                    dt = dt.astimezone(TIMEZONE)
                sort_key = dt
                if dt.hour == 0 and dt.minute == 0:
                    linha = tarefa.title
                else:
                    linha = f"{dt.strftime('%H:%M')} — {tarefa.title}"
            else:
                sort_key = None
                linha = tarefa.title
            usuarios[tarefa.user_id].append((sort_key, linha))

        for user_id, tarefas_raw in usuarios.items():
            # Ordena: com horário primeiro (crescente), sem prazo no final
            tarefas_raw.sort(key=lambda x: (x[0] is None, x[0] or datetime.min))
            tarefas = [texto for _, texto in tarefas_raw]
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
    Envia briefing de "dia livre" para o dono do bot via ALLOWED_CHAT_ID.
    """
    if not ALLOWED_CHAT_ID:
        logger.warning("[Scheduler] ALLOWED_CHAT_ID não configurado, briefing vazio pulado.")
        return

    enviado = await enviar_briefing(ALLOWED_CHAT_ID, [])
    if enviado:
        logger.info(f"[Scheduler] Briefing 'dia livre' enviado para {ALLOWED_CHAT_ID}")


async def limpar_historico_antigo():
    """
    Remove mensagens do conversation_history com mais de 30 dias.
    Mantém o banco enxuto sem impactar o contexto das conversas recentes.
    """
    db = SessionLocal()
    try:
        limite = datetime.now(TIMEZONE) - timedelta(days=30)
        deletados = db.query(ConversationHistory).filter(
            ConversationHistory.created_at < limite
        ).delete()
        db.commit()
        if deletados:
            logger.info(f"[Scheduler] {deletados} mensagem(ns) antigas do histórico removidas.")
    except Exception as e:
        db.rollback()
        logger.error(f"[Scheduler] Erro ao limpar histórico: {e}")
    finally:
        db.close()


async def limpar_updates_antigos():
    """
    Remove registros de processed_updates com mais de 7 dias.
    Roda diariamente para evitar crescimento indefinido da tabela.
    """
    db = SessionLocal()
    try:
        limite = datetime.now(TIMEZONE) - timedelta(days=7)
        deletados = db.query(ProcessedUpdate).filter(
            ProcessedUpdate.created_at < limite
        ).delete()
        db.commit()
        if deletados:
            logger.info(f"[Scheduler] {deletados} update(s) antigos removidos.")
    except Exception as e:
        db.rollback()
        logger.error(f"[Scheduler] Erro ao limpar updates antigos: {e}")
    finally:
        db.close()


def _planejamento_feito_hoje(user_id: str) -> bool:
    """Verifica se finalizar_planejamento já foi chamado hoje para este usuário."""
    db = SessionLocal()
    try:
        # created_at no ToolCallLog é naive UTC — compara com início do dia em UTC
        import pytz
        agora_utc = datetime.utcnow()
        inicio_dia_utc = agora_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        return db.query(ToolCallLog).filter(
            ToolCallLog.user_id == user_id,
            ToolCallLog.tool_name == "finalizar_planejamento",
            ToolCallLog.created_at >= inicio_dia_utc,
        ).first() is not None
    except Exception as e:
        logger.error(f"[_planejamento_feito_hoje] {e}")
        return False
    finally:
        db.close()


def buscar_tarefas_hoje(user_id: str) -> list:
    """Retorna tarefas pendentes com due_date para hoje (timezone-aware)."""
    db = SessionLocal()
    try:
        agora = datetime.now(TIMEZONE)
        inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        fim = agora.replace(hour=23, minute=59, second=59, microsecond=0)
        return (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.due_date >= inicio,
                Task.due_date <= fim,
            )
            .all()
        )
    except Exception as e:
        logger.error(f"[buscar_tarefas_hoje] {e}")
        return []
    finally:
        db.close()


async def iniciar_planejamento_manual(user_id: str) -> bool:
    """
    Inicia o planejamento quando acionado pelo usuário via chat (/planejar).
    Mesma lógica do scheduler — com revisão de tarefas — mas sem verificar
    se o planejamento já foi feito hoje e sem enviar mensagem de abertura.

    Returns:
        True  → teclado de revisão foi enviado (caller não precisa fazer mais nada).
        False → estado setado para planning, caller deve chamar chat() normalmente
                para que a IA responda à mensagem original de forma natural.
    """
    logger.info(f"[Manual] Iniciando planejamento para {user_id}...")
    limpar_historico_planning(user_id)

    tarefas_hoje = buscar_tarefas_hoje(user_id)
    if tarefas_hoje:
        enviado = await enviar_revisao_tarefas(user_id, tarefas_hoje)
        if enviado:
            set_session_state(user_id, "reviewing_tasks")
            return True
        logger.warning("[Manual] Falha ao enviar revisão, indo direto ao planejamento.")

    set_session_state(user_id, "planning")
    return False  # caller continua com chat() para resposta natural da IA


async def iniciar_planejamento():
    """
    Inicia a sessão de planejamento noturno (via scheduler).
    Se houver tarefas planejadas para hoje, exibe revisão via inline keyboard
    (estado reviewing_tasks) antes de abrir a conversa de planejamento.
    Pula se o usuário já planejou o dia manualmente antes das 21h.
    """
    if not ALLOWED_CHAT_ID:
        logger.warning("[Scheduler] ALLOWED_CHAT_ID não configurado, planejamento pulado.")
        return

    if _planejamento_feito_hoje(ALLOWED_CHAT_ID):
        logger.info("[Scheduler] Planejamento já realizado hoje. Sessão das 21h ignorada.")
        return

    logger.info(f"[Scheduler] Iniciando sessão de planejamento para {ALLOWED_CHAT_ID}...")
    limpar_historico_planning(ALLOWED_CHAT_ID)

    tarefas_hoje = buscar_tarefas_hoje(ALLOWED_CHAT_ID)
    if tarefas_hoje:
        logger.info(f"[Scheduler] {len(tarefas_hoje)} tarefa(s) para revisar hoje.")
        enviado = await enviar_revisao_tarefas(ALLOWED_CHAT_ID, tarefas_hoje)
        if enviado:
            set_session_state(ALLOWED_CHAT_ID, "reviewing_tasks")
            logger.info("[Scheduler] Revisão de tarefas enviada, aguardando interação do usuário.")
            return
        logger.warning("[Scheduler] Falha ao enviar revisão, indo direto ao planejamento.")

    set_session_state(ALLOWED_CHAT_ID, "planning")
    enviado = await enviar_inicio_planejamento(ALLOWED_CHAT_ID)
    if enviado:
        logger.info("[Scheduler] Sessão de planejamento iniciada (sem tarefas para revisar).")
    else:
        logger.warning("[Scheduler] Falha ao enviar abertura do planejamento.")


async def briefing_catchup():
    """
    Verifica se o servidor reiniciou logo após o horário do briefing e o envio foi perdido.
    Só dispara se o restart aconteceu dentro de 2 horas após o horário do briefing —
    evita reenviar o briefing quando o servidor reinicia no período da tarde/noite.
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

    segundos_apos_briefing = (agora - horario_briefing).total_seconds()
    janela_catchup = 2 * 3600  # 2 horas

    if 0 <= segundos_apos_briefing <= janela_catchup:
        logger.info(
            f"[Scheduler-Catchup] Restart dentro da janela do briefing "
            f"({segundos_apos_briefing / 60:.0f}min após {BRIEFING_HORA}). Enviando catchup..."
        )
        await briefing_diario(forçar_envio=True)
    else:
        logger.info(
            f"[Scheduler-Catchup] Fora da janela de catchup "
            f"(briefing às {BRIEFING_HORA}, agora {agora.strftime('%H:%M')}). Pulando."
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

    scheduler.add_job(
        limpar_historico_antigo,
        "cron",
        hour=3,
        minute=30,
        id="limpar_historico_antigo",
        name="Remove histórico de conversa com mais de 30 dias",
        replace_existing=True,
        timezone=TIMEZONE,
    )

    scheduler.add_job(
        limpar_updates_antigos,
        "cron",
        hour=3,
        minute=0,
        id="limpar_updates_antigos",
        name="Remove processed_updates com mais de 7 dias",
        replace_existing=True,
        timezone=TIMEZONE,
    )

    hora_checkin, minuto_checkin = map(int, CHECKIN_HORA.split(":"))
    scheduler.add_job(
        iniciar_planejamento,
        "cron",
        hour=hora_checkin,
        minute=minuto_checkin,
        id="iniciar_planejamento",
        name="Inicia sessão de planejamento noturno",
        replace_existing=True,
        timezone=TIMEZONE,
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
        f"briefing às {BRIEFING_HORA}, planejamento às {CHECKIN_HORA}"
    )
