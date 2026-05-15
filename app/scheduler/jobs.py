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
import uuid
import secrets
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
    enviar_pergunta_data_planejamento,
    enviar_revisao_tarefas,
    enviar_mensagem,
)
from app.agent.session import set_session_state, get_session_context
from app.agent.sara_agent import limpar_historico_planning
from app.agent.dates import next_day_iso, parse_explicit_or_relative_date
from app.agent.copy import (
    mensagem_abertura_planejamento,
    mensagem_pergunta_data_planejamento,
    mensagem_revisao_backlog_disponivel,
    mensagem_revisao_check,
    mensagem_revisao_planejamento,
)
from app.models.tool_call_log import ToolCallLog
from app.config import BRIEFING_HORA, CHECKIN_HORA, ALLOWED_CHAT_ID
from app.agent.tools import briefing_do_dia, sincronizar_categorias_pendentes, tarefas_backlog_pendentes

logger = logging.getLogger(__name__)

TIMEZONE = pytz.timezone("America/Sao_Paulo")

# Flag para controlar se o catchup do briefing já rodou
briefing_catchup_done = False


def _amanha_logico_iso(agora: datetime | None = None) -> str:
    from app.agent.tools import hoje_logico
    if agora is None:
        agora = datetime.now(TIMEZONE)
    return next_day_iso(hoje_logico(agora))


def _checkin_alcancado(agora: datetime | None = None) -> bool:
    if agora is None:
        agora = datetime.now(TIMEZONE)
    hora, minuto = map(int, CHECKIN_HORA.split(":"))
    corte = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return agora >= corte


def _parse_data_explicita(mensagem: str, agora: datetime | None = None) -> str | None:
    if not mensagem:
        return None
    if agora is None:
        agora = datetime.now(TIMEZONE)
    return parse_explicit_or_relative_date(mensagem, now=agora)


def resolver_data_alvo_manual(mensagem: str, agora: datetime | None = None) -> str | None:
    if agora is None:
        agora = datetime.now(TIMEZONE)
    data_explicita = _parse_data_explicita(mensagem, agora)
    if data_explicita:
        return data_explicita
    if _checkin_alcancado(agora):
        return _amanha_logico_iso(agora)
    return None


def _buscar_tarefas_por_ids(user_id: str, task_ids: list[str]) -> list[Task]:
    if not task_ids:
        return []
    uuids: list[uuid.UUID] = []
    for task_id in task_ids:
        try:
            uuids.append(uuid.UUID(str(task_id)))
        except ValueError:
            continue

    if not uuids:
        return []

    db = SessionLocal()
    try:
        tarefas = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.id.in_(uuids),
                Task.status == "pending",
            )
            .all()
        )
        ordem = {str(task_id): idx for idx, task_id in enumerate(task_ids)}
        tarefas.sort(key=lambda task: ordem.get(str(task.id), 9999))
        return tarefas
    finally:
        db.close()


def _serializar_tarefas_revisao(tarefas: list[Task]) -> list[dict]:
    return [{"task_id": str(task.id), "title": task.title} for task in tarefas]


def _novo_contexto_revisao(
    tarefas: list[Task],
    *,
    review_mode: str,
    target_date: str | None,
    awaiting_target_date: bool,
) -> tuple[str, dict]:
    review_session_id = secrets.token_hex(8)
    review_tasks = _serializar_tarefas_revisao(tarefas)
    status_map = {task["task_id"]: False for task in review_tasks}
    contexto = {
        "review_session_id": review_session_id,
        "review_mode": review_mode,
        "review_task_ids": [task["task_id"] for task in review_tasks],
        "review_tasks": review_tasks,
        "review_task_status_map": status_map,
        "target_date": target_date,
        "awaiting_target_date": awaiting_target_date,
        "review_done": False,
        "remaining_pending": [],
    }
    return review_session_id, contexto


async def abrir_fluxo_pos_revisao(user_id: str) -> None:
    from app.agent.sara_agent import finalizar_revisao

    resposta = finalizar_revisao(user_id)
    await enviar_mensagem(user_id, resposta)


def iniciar_revisao_check(user_id: str) -> tuple[bool, str]:
    tarefas_hoje = buscar_tarefas_hoje(user_id, only_past=False)
    if not tarefas_hoje:
        backlog = tarefas_backlog_pendentes(user_id)
        if backlog:
            review_tasks = _serializar_tarefas_revisao(backlog)
            set_session_state(
                user_id,
                "confirming_backlog_review",
                context={"backlog_review_tasks": review_tasks},
                replace_context=True,
            )
            return True, mensagem_revisao_backlog_disponivel(review_tasks)
        return True, mensagem_revisao_check([])

    review_session_id, contexto = _novo_contexto_revisao(
        tarefas_hoje,
        review_mode="check",
        target_date=None,
        awaiting_target_date=False,
    )
    set_session_state(user_id, "reviewing_tasks", context=contexto, replace_context=True)
    texto = mensagem_revisao_check(contexto["review_tasks"])
    return True, texto


async def iniciar_revisao_check_manual(user_id: str) -> bool:
    tarefas_hoje = buscar_tarefas_hoje(user_id, only_past=False)
    if not tarefas_hoje:
        backlog = tarefas_backlog_pendentes(user_id)
        if backlog:
            review_tasks = _serializar_tarefas_revisao(backlog)
            set_session_state(
                user_id,
                "confirming_backlog_review",
                context={"backlog_review_tasks": review_tasks},
                replace_context=True,
            )
            await enviar_mensagem(user_id, mensagem_revisao_backlog_disponivel(review_tasks))
            return True
        await enviar_mensagem(user_id, mensagem_revisao_check([]))
        return True

    review_session_id, contexto = _novo_contexto_revisao(
        tarefas_hoje,
        review_mode="check",
        target_date=None,
        awaiting_target_date=False,
    )
    set_session_state(user_id, "reviewing_tasks", context=contexto, replace_context=True)
    return await enviar_revisao_tarefas(
        user_id,
        tarefas_hoje,
        mensagem_revisao_check(contexto["review_tasks"]),
        review_session_id,
    )


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
        sincronizar_categorias_pendentes(db)
        user_ids = [row[0] for row in db.query(Task.user_id).filter(Task.status == "pending").distinct().all()]

        if not user_ids:
            if forçar_envio:
                logger.info("[Scheduler] Sem tarefas, enviando 'dia livre'")
                await _enviar_briefing_vazio()
            else:
                logger.info("[Scheduler] Sem tarefas para hoje, briefing pulado")
            return

        for user_id in user_ids:
            texto = briefing_do_dia(user_id)
            enviado = await enviar_briefing(user_id, texto)
            if enviado:
                logger.info(f"[Scheduler] Briefing enviado para {user_id}")
            else:
                logger.warning(f"[Scheduler] Falha ao enviar briefing para {user_id}")

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
    """Verifica se finalizar_planejamento já foi chamado no dia lógico atual."""
    db = SessionLocal()
    try:
        from app.agent.tools import intervalo_dia_logico
        inicio_local, _ = intervalo_dia_logico()
        # ToolCallLog.created_at é naive UTC — converte início do dia lógico para UTC naive
        inicio_dia_utc = inicio_local.astimezone(pytz.utc).replace(tzinfo=None)
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


def buscar_tarefas_hoje(user_id: str, only_past: bool = False) -> list:
    """
    Retorna tarefas pendentes com due_date para o dia lógico atual.

    Args:
        only_past: Se True, exclui tarefas cuja hora ainda não passou. Útil para
                   trigger manual no meio do dia (não pede pra revisar tarefa futura).
                   Tarefas sem hora marcada (meia-noite) sempre entram.
    """
    db = SessionLocal()
    try:
        sincronizar_categorias_pendentes(db, user_id=user_id)
        from app.agent.tools import intervalo_dia_logico
        agora = datetime.now(TIMEZONE)
        inicio, fim = intervalo_dia_logico(agora)

        tarefas = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.due_date >= inicio,
                Task.due_date <= fim,
            )
            .all()
        )

        if not only_past:
            return tarefas

        def _ja_passou(t: Task) -> bool:
            dt = t.due_date
            if dt is None:
                return True
            if dt.tzinfo is None:
                dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
            else:
                dt = dt.astimezone(TIMEZONE)
            # Sem hora marcada (meia-noite) → considera que ainda vale revisar
            if dt.hour == 0 and dt.minute == 0:
                return True
            return dt <= agora

        return [t for t in tarefas if _ja_passou(t)]
    except Exception as e:
        logger.error(f"[buscar_tarefas_hoje] {e}")
        return []
    finally:
        db.close()


async def iniciar_planejamento_manual(user_id: str, mensagem: str) -> bool:
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
    agora = datetime.now(TIMEZONE)
    target_date = resolver_data_alvo_manual(mensagem, agora)

    # only_past=True: no trigger manual mostra só tarefas cuja hora já passou,
    # não faz sentido pedir revisão de algo que ainda vai acontecer.
    tarefas_hoje = buscar_tarefas_hoje(user_id, only_past=True)
    if tarefas_hoje:
        review_session_id, contexto = _novo_contexto_revisao(
            tarefas_hoje,
            review_mode="planning",
            target_date=target_date,
            awaiting_target_date=target_date is None,
        )
        set_session_state(
            user_id,
            "reviewing_tasks",
            context=contexto,
            replace_context=True,
        )
        enviado = await enviar_revisao_tarefas(
            user_id,
            tarefas_hoje,
            mensagem_revisao_planejamento(contexto["review_tasks"]),
            review_session_id,
        )
        if enviado:
            return True
        logger.warning("[Manual] Falha ao enviar revisão, indo direto ao planejamento.")

    if target_date:
        from app.agent.sara_agent import salvar_historico

        abertura = mensagem_abertura_planejamento(target_date)
        set_session_state(
            user_id,
            "planning",
            context={
                "target_date": target_date,
                "awaiting_target_date": False,
                "review_done": False,
                "remaining_pending": [],
                "review_mode": "planning",
            },
            replace_context=True,
        )
        await enviar_inicio_planejamento(user_id, target_date)
        salvar_historico(user_id, "plan_asst", abertura)
        return True

    set_session_state(
        user_id,
        "planning",
        context={
            "target_date": None,
            "awaiting_target_date": True,
            "review_done": False,
            "remaining_pending": [],
            "review_mode": "planning",
        },
        replace_context=True,
    )
    await enviar_pergunta_data_planejamento(user_id)
    return True


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
    target_date = _amanha_logico_iso()

    tarefas_hoje = buscar_tarefas_hoje(ALLOWED_CHAT_ID)
    if tarefas_hoje:
        logger.info(f"[Scheduler] {len(tarefas_hoje)} tarefa(s) para revisar hoje.")
        review_session_id, contexto = _novo_contexto_revisao(
            tarefas_hoje,
            review_mode="planning",
            target_date=target_date,
            awaiting_target_date=False,
        )
        set_session_state(
            ALLOWED_CHAT_ID,
            "reviewing_tasks",
            context=contexto,
            replace_context=True,
        )
        enviado = await enviar_revisao_tarefas(
            ALLOWED_CHAT_ID,
            tarefas_hoje,
            mensagem_revisao_planejamento(contexto["review_tasks"]),
            review_session_id,
        )
        if enviado:
            logger.info("[Scheduler] Revisão de tarefas enviada, aguardando interação do usuário.")
            return
        logger.warning("[Scheduler] Falha ao enviar revisão, indo direto ao planejamento.")

    from app.agent.sara_agent import salvar_historico

    abertura = mensagem_abertura_planejamento(target_date)
    set_session_state(
        ALLOWED_CHAT_ID,
        "planning",
        context={
            "target_date": target_date,
            "awaiting_target_date": False,
            "review_done": False,
            "remaining_pending": [],
            "review_mode": "planning",
        },
        replace_context=True,
    )
    enviado = await enviar_inicio_planejamento(ALLOWED_CHAT_ID, target_date)
    if enviado:
        salvar_historico(ALLOWED_CHAT_ID, "plan_asst", abertura)
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
