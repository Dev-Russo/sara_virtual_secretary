#!/usr/bin/env python3
"""
Testes de validação pós-deploy.

Cobre:
  - Conexão com o banco
  - Detecção de keywords de planejamento (incluindo negação)
  - Formato do briefing (horário vs sem horário)
  - Filtro de tarefas por data (buscar_tarefas_hoje)
  - Fluxo de planejamento com tarefas → keyboard enviado, estado reviewing_tasks
  - Fluxo de planejamento sem tarefas → estado planning, sem mensagem automática
  - Marcar tarefa como concluída via callback
  - Transições de estado (idle / reviewing_tasks / planning)

Uso:
  python3 test_deploy.py
"""

import asyncio
import sys
from datetime import datetime, timedelta
from contextlib import contextmanager
from types import SimpleNamespace

import pytz

TIMEZONE = pytz.timezone("America/Sao_Paulo")
TEST_USER = "test_deploy_sara"

# ─── Monkey-patch Telegram ANTES de qualquer outro import ─────────────────
from tests.harness.telegram import install_fake_telegram

_sent_messages: list[str] = []
_sent_keyboards: list[dict] = []

_telegram_capture = install_fake_telegram()
_telegram_capture.messages = _sent_messages
_telegram_capture.keyboards = _sent_keyboards

# ─── Imports após patch ────────────────────────────────────────────────────
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from app.models.task import Task
from app.models.user_session import UserSession
from app.agent.session import get_session_state, set_session_state, get_session_context
from app.agent.prompts import get_system_prompt
from app.agent.sara_agent import (
    _quer_iniciar_planejamento,
    _quer_sair_planejamento,
    _confirmou_plano,
    carregar_historico,
    chat,
    toggle_review_task,
    finalizar_revisao,
    salvar_historico,
)
from app.agent.tools import TOOLS_MAP, TOOLS_SCHEMA, list_tasks
import app.scheduler.jobs as jobs
from app.agent import sara_agent as sara_agent_module
from app.scheduler.jobs import iniciar_planejamento_manual, buscar_tarefas_hoje, abrir_fluxo_pos_revisao, iniciar_revisao_check
from app.agent.tools import complete_task

# ─── Helpers ──────────────────────────────────────────────────────────────

_results: list[tuple[str, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    mark = "✓" if condition else "✗"
    print(f"  {mark} {name}" + (f"\n      → {detail}" if detail and not condition else ""))
    _results.append((name, condition))
    return condition


def _create_task(title: str, due_date=None, status: str = "pending") -> str:
    db = SessionLocal()
    try:
        task = Task(user_id=TEST_USER, title=title, due_date=due_date, status=status)
        db.add(task)
        db.commit()
        db.refresh(task)
        return str(task.id)
    finally:
        db.close()


def _cleanup():
    db = SessionLocal()
    try:
        db.query(ConversationHistory).filter(ConversationHistory.user_id == TEST_USER).delete()
        db.query(Task).filter(Task.user_id == TEST_USER).delete()
        db.query(UserSession).filter(UserSession.user_id == TEST_USER).delete()
        db.commit()
    finally:
        db.close()


def _reset_capture():
    _sent_messages.clear()
    _sent_keyboards.clear()
    _telegram_capture.messages.clear()
    _telegram_capture.keyboards.clear()
    _telegram_capture.edits.clear()
    _telegram_capture.callbacks.clear()


def _sync_capture():
    _sent_messages[:] = list(_telegram_capture.messages)
    _sent_keyboards[:] = [
        {"text": item["text"], "markup": item["markup"]}
        for item in _telegram_capture.keyboards
    ]


@contextmanager
def _freeze_jobs_now(frozen_dt: datetime):
    real_datetime = jobs.datetime

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_dt.replace(tzinfo=None)
            return frozen_dt.astimezone(tz)

    jobs.datetime = _FrozenDateTime
    try:
        yield
    finally:
        jobs.datetime = real_datetime


@contextmanager
def _mock_anthropic_responses(responses: list[object]):
    original_create = sara_agent_module.anthropic_client.messages.create
    queue = list(responses)

    def fake_create(*args, **kwargs):
        if not queue:
            raise AssertionError("Anthropic response queue exhausted")
        return queue.pop(0)

    sara_agent_module.anthropic_client.messages.create = fake_create
    try:
        yield
    finally:
        sara_agent_module.anthropic_client.messages.create = original_create


# ─── Testes ───────────────────────────────────────────────────────────────

def test_db():
    print("\n[1] Conexão com o banco")
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        check("conexão PostgreSQL OK", True)
    except Exception as e:
        check("conexão PostgreSQL OK", False, str(e))


def test_keywords():
    print("\n[2] Detecção de keywords de planejamento")

    positivos = [
        "/planejar",
        "vamos planejar",
        "quero planejar",
        "me ajuda a planejar",
        "iniciar planejamento",
        "planeje meu dia",
        "planejae meu amanhã",
    ]
    negativos = [
        "não quero planejar",
        "nao quero planejar",
        "não vou planejar",
        "hoje não quero planejar nada",
        "não quero planear",
    ]

    for msg in positivos:
        check(f'"{msg}" → ativa', _quer_iniciar_planejamento(msg))
    for msg in negativos:
        check(f'"{msg}" → NÃO ativa', not _quer_iniciar_planejamento(msg))

    check('"não quero mais" → sai do fluxo', _quer_sair_planejamento("não quero mais"))
    check(
        '"sim" após confirmação do plano → confirma plano',
        _confirmou_plano(
            [{"role": "assistant", "content": "Tudo certo? Faz sentido assim?"}],
            "sim",
        ),
    )


def test_briefing_format():
    print("\n[3] Formatação do briefing")
    agora = datetime.now(TIMEZONE)

    # Tarefa com horário definido (não meia-noite)
    dt_hora = agora.replace(hour=9, minute=30, second=0, microsecond=0)
    if not (dt_hora.hour == 0 and dt_hora.minute == 0):
        linha = f"{dt_hora.strftime('%H:%M')} — Reunião"
    else:
        linha = "Reunião"
    check('"09:30 — Reunião" exibe horário', "09:30 — Reunião" == linha)

    # Tarefa só com data (meia-noite = sem hora)
    dt_meia_noite = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    if not (dt_meia_noite.hour == 0 and dt_meia_noite.minute == 0):
        linha2 = f"{dt_meia_noite.strftime('%H:%M')} — Academia"
    else:
        linha2 = "Academia"
    check('"Academia" (meia-noite) omite horário', linha2 == "Academia")

    # Tarefa sem due_date
    linha3 = "Ligar pro médico"
    check('"Ligar pro médico" (sem data) só título', linha3 == "Ligar pro médico")


def test_buscar_tarefas_hoje():
    print("\n[4] buscar_tarefas_hoje — filtro por data")
    agora = datetime.now(TIMEZONE)

    hoje = agora.replace(hour=10, minute=0, second=0, microsecond=0)
    ontem = (agora - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    amanha = (agora + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

    _create_task("Tarefa hoje", due_date=hoje)
    _create_task("Tarefa ontem", due_date=ontem)
    _create_task("Tarefa amanhã", due_date=amanha)
    _create_task("Tarefa sem data")
    _create_task("Tarefa hoje já feita", due_date=hoje, status="done")

    tarefas = buscar_tarefas_hoje(TEST_USER)
    titulos = [t.title for t in tarefas]

    check("retorna tarefa de hoje", "Tarefa hoje" in titulos)
    check("não retorna tarefa de ontem", "Tarefa ontem" not in titulos)
    check("não retorna tarefa de amanhã", "Tarefa amanhã" not in titulos)
    check("não retorna tarefa sem data", "Tarefa sem data" not in titulos)
    check("não retorna tarefa já concluída", "Tarefa hoje já feita" not in titulos)

    _cleanup()


async def test_planning_com_tarefas():
    print("\n[5] Planejamento manual — com tarefas para hoje")
    _reset_capture()

    agora = datetime.now(TIMEZONE).replace(hour=20, minute=30, second=0, microsecond=0)
    hoje = agora.replace(hour=14, minute=0, second=0, microsecond=0)
    _create_task("Tarefa de revisão", due_date=hoje)

    with _freeze_jobs_now(agora):
        handled = await iniciar_planejamento_manual(TEST_USER, "/planejar")
    state = get_session_state(TEST_USER)
    contexto = get_session_context(TEST_USER)

    check("retornou True (keyboard enviado)", handled, f"retornou: {handled}")
    check("estado → reviewing_tasks", state == "reviewing_tasks", f"estado: {state}")
    check("aguardando data alvo após revisão", contexto.get("awaiting_target_date") is True,
          f"contexto: {contexto}")
    check("keyboard foi enviado", len(_sent_keyboards) == 1, f"qtd keyboards: {len(_sent_keyboards)}")
    check("mensagem de revisão enviada", any("seperei" in msg.lower() or "separei" in msg.lower() for msg in _sent_messages),
          f"msgs: {_sent_messages}")

    if _sent_keyboards:
        markup = _sent_keyboards[0]["markup"]
        buttons = [btn for row in markup.inline_keyboard for btn in row]
        labels = [btn.text for btn in buttons]
        check('botão com "☐" presente', any("☐" in lb for lb in labels))
        check('botão "Fechar revisão" presente', any("Fechar revisão" in lb for lb in labels))

    _cleanup()


async def test_planning_sem_tarefas_noite():
    print("\n[6] Planejamento manual — sem tarefas às 20:30")
    _reset_capture()

    agora = datetime.now(TIMEZONE).replace(hour=20, minute=30, second=0, microsecond=0)
    with _freeze_jobs_now(agora):
        handled = await iniciar_planejamento_manual(TEST_USER, "/planejar")
    state = get_session_state(TEST_USER)
    contexto = get_session_context(TEST_USER)

    check("retornou True (fluxo tratado no orquestrador)", handled, f"retornou: {handled}")
    check("estado → planning", state == "planning", f"estado: {state}")
    check("aguardando data alvo", bool(contexto.get("awaiting_target_date")),
          f"contexto: {contexto}")
    check("perguntou hoje ou amanhã", any("organizar hoje ou amanhã" in msg.lower() for msg in _sent_messages), f"msgs: {_sent_messages}")
    check("nenhum inline keyboard de revisão enviado", not any(hasattr(item["markup"], "inline_keyboard") for item in _sent_keyboards))

    _cleanup()


async def test_planning_sem_tarefas_manha():
    print("\n[7] Planejamento manual — sem tarefas às 10:00")
    _reset_capture()

    agora = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    with _freeze_jobs_now(agora):
        handled = await iniciar_planejamento_manual(TEST_USER, "/planejar")
    state = get_session_state(TEST_USER)
    contexto = get_session_context(TEST_USER)

    check("retornou True", handled, f"retornou: {handled}")
    check("estado → planning", state == "planning", f"estado: {state}")
    check("aguardando data alvo", bool(contexto.get("awaiting_target_date")), f"contexto: {contexto}")
    check("perguntou qual dia planejar", any("organizar hoje ou amanhã" in msg.lower() for msg in _sent_messages),
          f"msgs: {_sent_messages}")

    _cleanup()


async def test_planning_com_data_explicita():
    print("\n[8] Planejamento manual — data explícita na mensagem")
    _reset_capture()

    agora = TIMEZONE.localize(datetime(2026, 4, 28, 10, 0))
    with _freeze_jobs_now(agora):
        handled = await iniciar_planejamento_manual(TEST_USER, "/planejar 30/04")
    contexto = get_session_context(TEST_USER)

    check("retornou True", handled, f"retornou: {handled}")
    check("usou a data mencionada", contexto.get("target_date") == "2026-04-30", f"contexto: {contexto}")
    check("não ficou esperando data", not contexto.get("awaiting_target_date"), f"contexto: {contexto}")

    _cleanup()


async def test_toggle_revisao():
    print("\n[9] Toggle de tarefa na revisão")
    _reset_capture()
    agora = datetime.now(TIMEZONE).replace(hour=20, minute=30, second=0, microsecond=0)
    hoje = agora.replace(hour=14, minute=0, second=0, microsecond=0)
    task_id = _create_task("Tarefa para toggle", due_date=hoje)

    with _freeze_jobs_now(agora):
        await iniciar_planejamento_manual(TEST_USER, "/planejar")

    marcado = toggle_review_task(TEST_USER, task_id)
    desmarcado = toggle_review_task(TEST_USER, task_id)
    contexto = get_session_context(TEST_USER)

    check("primeiro toque marca", marcado is True)
    check("segundo toque desmarca", desmarcado is False)
    check("estado salvo no contexto", contexto.get("review_task_status_map", {}).get(task_id) is False,
          f"contexto: {contexto}")

    _cleanup()


def test_estados():
    print("\n[10] Transições de estado")
    for estado in ("idle", "reviewing_tasks", "review_confirming", "planning", "idle"):
        set_session_state(TEST_USER, estado)
        atual = get_session_state(TEST_USER)
        check(f"setar → {estado}", atual == estado, f"ficou: {atual}")

    _cleanup()


async def test_fluxo_revisao_em_lote():
    print("\n[11] Revisão em lote — conclui e move sem duplicar")
    _reset_capture()

    agora = datetime.now(TIMEZONE).replace(hour=20, minute=30, second=0, microsecond=0)
    hoje = agora.replace(hour=14, minute=0, second=0, microsecond=0)
    task_id = _create_task("Enviar relatório", due_date=hoje)
    _create_task("Estudar", due_date=hoje)

    with _freeze_jobs_now(agora):
        await iniciar_planejamento_manual(TEST_USER, "/planejar 2026-05-03")
    resposta = chat("fiz enviar relatório, não estudei", user_id=TEST_USER)
    resposta_final = chat("ok", user_id=TEST_USER)

    db = SessionLocal()
    tarefas = db.query(Task).filter(Task.user_id == TEST_USER).all()
    tarefa = db.query(Task).filter(Task.id == task_id).first()
    estudar = db.query(Task).filter(Task.title == "Estudar", Task.user_id == TEST_USER).first()
    db.close()

    check("gerou confirmação em lote", "então ficou assim" in resposta.lower(), f"resposta: {resposta}")
    check("não criou duplicata", len(tarefas) == 2, f"qtd: {len(tarefas)}")
    check("marcou feita a concluída", tarefa is not None and tarefa.status == "done",
          f"status: {tarefa.status if tarefa else 'não encontrada'}")
    check("moveu a pendente para a data alvo", estudar is not None and estudar.due_date.strftime("%Y-%m-%d") == "2026-05-03",
          f"due_date: {estudar.due_date if estudar else 'não encontrada'}")
    check("seguiu para o planejamento", "o que precisa acontecer" in resposta_final.lower(), f"resposta: {resposta_final}")

    _cleanup()


async def test_revisao_check():
    print("\n[12] /check abre revisão sem planejamento")
    _reset_capture()

    agora = datetime.now(TIMEZONE).replace(hour=12, minute=0, second=0, microsecond=0)
    hoje = agora.replace(hour=9, minute=0, second=0, microsecond=0)
    _create_task("Academia", due_date=hoje)

    handled, resposta = iniciar_revisao_check(TEST_USER)
    estado = get_session_state(TEST_USER)

    check("iniciou /check", handled, f"resposta: {resposta}")
    check("estado em revisão", estado == "reviewing_tasks", f"estado: {estado}")
    check("mensagem pede revisão", "me fala o que" in resposta.lower(), f"resposta: {resposta}")

    _cleanup()


def test_reagendar_backlog_deterministico():
    print("\n[13] Backlog → seleção antes de reagendar")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    _create_task("Revisar arquitetura da Sara")
    _create_task("Treinar")
    _create_task("Estudar docker")

    resposta = chat("Consegue passar as que estão no backlog para 2026-05-07?", user_id=TEST_USER)
    resposta_sem_horario = chat("Sem horário específico", user_id=TEST_USER)
    resposta_final = chat("Revisar arquitetura da Sara e Estudar docker", user_id=TEST_USER)

    db = SessionLocal()
    tarefas = db.query(Task).filter(Task.user_id == TEST_USER).order_by(Task.title.asc()).all()
    db.close()
    por_titulo = {t.title: t for t in tarefas}

    check("pediu quais tarefas mover", "quais tarefas" in resposta.lower(), f"resposta: {resposta}")
    check("não tratou sem horário como seleção", "quais tarefas" in resposta_sem_horario.lower(), f"resposta: {resposta_sem_horario}")
    check("confirmou reagendamento", "reagendei" in resposta_final.lower(), f"resposta: {resposta_final}")
    check(
        "moveu só as tarefas selecionadas",
        por_titulo["Revisar arquitetura da Sara"].due_date is not None
        and por_titulo["Estudar docker"].due_date is not None
        and por_titulo["Treinar"].due_date is None,
        f"datas: {[t.due_date for t in tarefas]}",
    )
    check(
        "usou a data pedida sem horário",
        por_titulo["Revisar arquitetura da Sara"].due_date is not None
        and por_titulo["Estudar docker"].due_date is not None
        and por_titulo["Revisar arquitetura da Sara"].due_date.astimezone(TIMEZONE).strftime("%Y-%m-%d") == "2026-05-07"
        and por_titulo["Estudar docker"].due_date.astimezone(TIMEZONE).strftime("%Y-%m-%d") == "2026-05-07"
        and por_titulo["Revisar arquitetura da Sara"].due_date.astimezone(TIMEZONE).hour == 0
        and por_titulo["Estudar docker"].due_date.astimezone(TIMEZONE).minute == 0,
        f"datas: {[t.due_date for t in tarefas]}",
    )

    _cleanup()


def test_tools_destrutivas_fora_do_schema_geral():
    print("\n[14] Tools destrutivas fora do schema geral do LLM")

    tool_names = {tool["name"] for tool in TOOLS_SCHEMA}

    check("delete_task não está no TOOLS_SCHEMA", "delete_task" not in tool_names, f"tools: {sorted(tool_names)}")
    check("delete_all_tasks não está no TOOLS_SCHEMA", "delete_all_tasks" not in tool_names, f"tools: {sorted(tool_names)}")
    check("delete_task continua disponível internamente", "delete_task" in TOOLS_MAP)
    check("delete_all_tasks continua disponível internamente", "delete_all_tasks" in TOOLS_MAP)


def test_prompt_bloqueia_delecao_no_fluxo_livre():
    print("\n[15] Prompt do fluxo livre bloqueia deleção")

    prompt = get_system_prompt(TEST_USER)

    check(
        "prompt menciona remoção fora do fluxo livre",
        "Operações destrutivas de deleção NÃO estão disponíveis neste fluxo livre." in prompt,
    )
    check(
        "prompt não anuncia delete_task como tool do fluxo livre",
        "delete_task, delete_all_tasks" not in prompt,
    )


def test_delete_deterministico_por_titulo():
    print("\n[16] Delete determinístico por título com confirmação")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    hoje = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    _create_task("Alongamento", due_date=hoje)
    _create_task("Treino", due_date=hoje)

    resposta = chat("apaga a tarefa Alongamento", user_id=TEST_USER)
    estado = get_session_state(TEST_USER)
    resposta_cancelada = chat("não", user_id=TEST_USER)

    db = SessionLocal()
    tarefas_restantes = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").order_by(Task.title.asc()).all()
    db.close()

    check("entrou em estado de confirmação", estado == "confirming_delete", f"estado: {estado}")
    check("mostrou preview da tarefa", "Alongamento" in resposta and "Confirmo?" in resposta, f"resposta: {resposta}")
    check("cancelamento não deleta", "não deletei nada" in resposta_cancelada.lower(), f"resposta: {resposta_cancelada}")
    check(
        "tarefas seguem intactas após cancelamento",
        sorted(task.title for task in tarefas_restantes) == ["Alongamento", "Treino"],
        f"tarefas: {[task.title for task in tarefas_restantes]}",
    )

    resposta = chat("apaga a tarefa Alongamento", user_id=TEST_USER)
    resposta_confirmada = chat("sim", user_id=TEST_USER)

    db = SessionLocal()
    tarefas_restantes = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").order_by(Task.title.asc()).all()
    db.close()

    check("preview reaparece na nova tentativa", "Alongamento" in resposta and "Confirmo?" in resposta, f"resposta: {resposta}")
    check("confirmação executa delete", "1 tarefa(s) deletada(s): Alongamento" in resposta_confirmada, f"resposta: {resposta_confirmada}")
    check(
        "removeu só a tarefa confirmada",
        [task.title for task in tarefas_restantes] == ["Treino"],
        f"tarefas: {[task.title for task in tarefas_restantes]}",
    )

    _cleanup()


def test_delete_deterministico_em_massa_por_data():
    print("\n[17] Delete determinístico em massa por data")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    hoje = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    amanha = (hoje + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    _create_task("Reunião hoje", due_date=hoje)
    _create_task("Estudo hoje", due_date=hoje)
    _create_task("Treino amanhã", due_date=amanha)

    resposta = chat("apaga todas as tarefas de hoje", user_id=TEST_USER)
    resposta_confirmada = chat("sim", user_id=TEST_USER)

    db = SessionLocal()
    tarefas_restantes = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").order_by(Task.title.asc()).all()
    db.close()

    check("preview mostra tarefas de hoje", "Reunião hoje" in resposta and "Estudo hoje" in resposta, f"resposta: {resposta}")
    check("preview pede confirmação", "Confirmo?" in resposta, f"resposta: {resposta}")
    check(
        "deletou só o conjunto congelado",
        "2 tarefa(s) deletada(s): Reunião hoje, Estudo hoje" in resposta_confirmada
        or "2 tarefa(s) deletada(s): Estudo hoje, Reunião hoje" in resposta_confirmada,
        f"resposta: {resposta_confirmada}",
    )
    check(
        "manteve tarefas fora do filtro",
        [task.title for task in tarefas_restantes] == ["Treino amanhã"],
        f"tarefas: {[task.title for task in tarefas_restantes]}",
    )

    _cleanup()


def test_preempcao_listagem_durante_revisao():
    print("\n[18] Preempção de listagem durante revisão")
    _reset_capture()
    set_session_state(TEST_USER, "reviewing_tasks", context={
        "review_mode": "check",
        "review_tasks": [],
        "review_task_status_map": {},
    }, replace_context=True)

    hoje = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    _create_task("Pendência em revisão", due_date=hoje)

    resposta = chat("Quais minhas tarefas pendentes?", user_id=TEST_USER)
    estado = get_session_state(TEST_USER)

    check("respondeu com listagem real", "Pendência em revisão" in resposta, f"resposta: {resposta}")
    check("saiu do estado de revisão", estado == "idle", f"estado: {estado}")

    _cleanup()


def test_preempcao_backlog_durante_planning():
    print("\n[19] Preempção de backlog durante planning")
    _reset_capture()
    set_session_state(TEST_USER, "planning", context={
        "target_date": "2026-05-20",
        "awaiting_target_date": False,
        "review_done": False,
        "remaining_pending": [],
    }, replace_context=True)

    _create_task("Item de backlog do planning")

    resposta = chat("Backlog", user_id=TEST_USER)
    estado = get_session_state(TEST_USER)

    check("respondeu com backlog real", "Item de backlog do planning" in resposta, f"resposta: {resposta}")
    check("saiu do estado de planning", estado == "idle", f"estado: {estado}")

    _cleanup()


def test_conclusao_em_massa_do_backlog_sem_pedir_data():
    print("\n[20] Conclusão em massa do backlog sem pedir data")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    hoje = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    _create_task("Backlog 1")
    _create_task("Backlog 2")
    _create_task("Tarefa datada", due_date=hoje)

    resposta = chat("marque todas as tarefas do backlog como concluídas", user_id=TEST_USER)
    estado = get_session_state(TEST_USER)
    resposta_confirmada = chat("sim", user_id=TEST_USER)

    db = SessionLocal()
    pendentes = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").order_by(Task.title.asc()).all()
    concluidas = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "done").order_by(Task.title.asc()).all()
    db.close()

    check("foi direto para confirmação do backlog", "tarefas do backlog" in resposta.lower() and "confirmo?" in resposta.lower(), f"resposta: {resposta}")
    check("entrou no estado de confirmação em massa", estado == "confirming_bulk_complete", f"estado: {estado}")
    check("não pediu período", "de qual período" not in resposta.lower(), f"resposta: {resposta}")
    check(
        "confirmou conclusão do backlog",
        "marquei como concluídas as tarefas do backlog" in resposta_confirmada.lower(),
        f"resposta: {resposta_confirmada}",
    )
    check(
        "concluiu só as tarefas sem data",
        [task.title for task in pendentes] == ["Tarefa datada"]
        and sorted(task.title for task in concluidas) == ["Backlog 1", "Backlog 2"],
        f"pendentes: {[task.title for task in pendentes]} | concluidas: {[task.title for task in concluidas]}",
    )

    _cleanup()


def test_conclusao_parcial_do_backlog_com_selecao():
    print("\n[21] Conclusão parcial do backlog com seleção explícita")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    _create_task("Backlog A")
    _create_task("Backlog B")
    _create_task("Backlog C")

    resposta = chat("marque tarefas do backlog como concluídas", user_id=TEST_USER)
    estado = get_session_state(TEST_USER)
    resposta_selecao = chat("1 e 3", user_id=TEST_USER)
    resposta_confirmada = chat("sim", user_id=TEST_USER)

    db = SessionLocal()
    pendentes = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").order_by(Task.title.asc()).all()
    concluidas = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "done").order_by(Task.title.asc()).all()
    db.close()

    check("listou opções do backlog", "quais tarefas do backlog" in resposta.lower(), f"resposta: {resposta}")
    check("entrou no estado de confirmação em massa", estado == "confirming_bulk_complete", f"estado: {estado}")
    check("seleção gerou preview", "Backlog A" in resposta_selecao and "Backlog C" in resposta_selecao and "Confirmo?" in resposta_selecao, f"resposta: {resposta_selecao}")
    check(
        "confirmou conclusão parcial",
        "marquei como concluídas as tarefas selecionadas do backlog" in resposta_confirmada.lower(),
        f"resposta: {resposta_confirmada}",
    )
    check(
        "concluiu apenas os itens escolhidos",
        [task.title for task in pendentes] == ["Backlog B"]
        and sorted(task.title for task in concluidas) == ["Backlog A", "Backlog C"],
        f"pendentes: {[task.title for task in pendentes]} | concluidas: {[task.title for task in concluidas]}",
    )

    _cleanup()


def test_write_tool_ignora_confirmacao_falsa_do_llm():
    print("\n[22] Write tool ignora confirmação falsa do LLM")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    tool_use_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use",
                name="save_task",
                input={"title": "Alongamento guiado"},
                id="toolu_test_save_task",
            )
        ],
    )
    fake_second_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="Feito! Já marquei como concluída.")],
    )

    with _mock_anthropic_responses([tool_use_response, fake_second_response]):
        resposta = chat("tenho que fazer alongamento guiado em breve", user_id=TEST_USER)

    db = SessionLocal()
    tarefas = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").all()
    db.close()

    check("retornou resultado da tool, não a frase falsa", "Tarefa 'Alongamento guiado' salva com sucesso" in resposta, f"resposta: {resposta}")
    check("não retornou confirmação mentirosa do segundo turno", "Já marquei como concluída" not in resposta, f"resposta: {resposta}")
    check("a tarefa foi persistida", any(task.title == "Alongamento guiado" for task in tarefas), f"tarefas: {[task.title for task in tarefas]}")

    _cleanup()


def test_revisao_confirma_so_o_que_persistiu():
    print("\n[23] Revisão confirma só o que persistiu")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    hoje = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    task_id = _create_task("Tarefa válida da revisão", due_date=hoje)

    contexto = {
        "review_mode": "check",
        "review_tasks": [
            {"task_id": task_id, "title": "Tarefa válida da revisão"},
            {"task_id": "00000000-0000-0000-0000-000000000001", "title": "Tarefa inválida da revisão"},
        ],
        "done_task_ids": [task_id, "00000000-0000-0000-0000-000000000001"],
        "pending_task_ids": [],
        "pending_action": "keep",
        "pending_date": None,
        "review_done": True,
    }
    set_session_state(TEST_USER, "review_confirming", context=contexto, replace_context=True)

    resposta = chat("ok", user_id=TEST_USER)

    db = SessionLocal()
    tarefa_valida = db.query(Task).filter(Task.id == task_id).first()
    db.close()

    check("confirmou só a tarefa válida", resposta.startswith("Marquei como feitas: Tarefa válida da revisão."), f"resposta: {resposta}")
    check("sinalizou falha no item inválido", "Não consegui aplicar tudo em: Tarefa inválida da revisão." in resposta, f"resposta: {resposta}")
    check("persistiu conclusão da tarefa válida", tarefa_valida is not None and tarefa_valida.status == "done", f"status: {tarefa_valida.status if tarefa_valida else 'não encontrada'}")

    _cleanup()


def test_complete_task_ambiguo_nao_muta():
    print("\n[24] complete_task ambíguo não muta")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    _create_task("Estudar inglês")
    _create_task("Estudar docker")

    resposta = complete_task("Estudar", TEST_USER)

    db = SessionLocal()
    pendentes = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").order_by(Task.title.asc()).all()
    concluidas = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "done").all()
    db.close()

    check("retornou mensagem de ambiguidade", "Encontrei mais de uma tarefa para concluir com 'Estudar'" in resposta, f"resposta: {resposta}")
    check("não concluiu nada por heurística frouxa", len(concluidas) == 0 and len(pendentes) == 2, f"pendentes: {[task.title for task in pendentes]} | concluidas: {len(concluidas)}")

    _cleanup()


def test_delete_ambiguo_pede_selecao():
    print("\n[25] Delete ambíguo pede seleção antes de confirmar")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    _create_task("Estudar inglês")
    _create_task("Estudar docker")

    resposta = chat("apaga estudar", user_id=TEST_USER)
    estado = get_session_state(TEST_USER)
    resposta_selecao = chat("2", user_id=TEST_USER)
    resposta_confirmada = chat("sim", user_id=TEST_USER)

    db = SessionLocal()
    pendentes = db.query(Task).filter(Task.user_id == TEST_USER, Task.status == "pending").order_by(Task.title.asc()).all()
    db.close()

    check("pediu seleção ao invés de deletar tudo", "Encontrei mais de uma tarefa para deletar" in resposta, f"resposta: {resposta}")
    check("entrou no estado de confirmação de delete", estado == "confirming_delete", f"estado: {estado}")
    check("seleção gerou preview do item escolhido", "Estudar inglês" not in resposta_selecao and "Estudar docker" in resposta_selecao and "Confirmo?" in resposta_selecao, f"resposta: {resposta_selecao}")
    check("deletou só o item escolhido", [task.title for task in pendentes] == ["Estudar inglês"], f"pendentes: {[task.title for task in pendentes]} | resposta: {resposta_confirmada}")

    _cleanup()


def test_listagem_ignora_categoria_persistida_stale():
    print("\n[26] Listagem ignora categoria persistida stale")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    hoje = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    db = SessionLocal()
    try:
        task = Task(
            user_id=TEST_USER,
            title="Tarefa com categoria stale",
            due_date=hoje,
            status="pending",
            category="backlog",
        )
        db.add(task)
        db.commit()
    finally:
        db.close()

    resposta = list_tasks(TEST_USER)

    check("classificou pela data real, não pela categoria stale", "Hoje:" in resposta and "Tarefa com categoria stale" in resposta and "Backlog:" not in resposta.split("Tarefa com categoria stale")[0], f"resposta: {resposta}")

    _cleanup()


def test_conclusao_em_massa_reconhece_minhas_atividades_de_hoje():
    print("\n[27] Conclusão em massa reconhece frase com minhas atividades de hoje")
    _reset_capture()
    set_session_state(TEST_USER, "idle")

    hoje = datetime.now(TIMEZONE).replace(hour=10, minute=0, second=0, microsecond=0)
    _create_task("Alongamento", due_date=hoje)

    resposta = chat("Marque minhas atividades de hoje como concluídas por favor", user_id=TEST_USER)
    estado = get_session_state(TEST_USER)

    check("entrou no fluxo determinístico em vez do LLM livre", "Vou marcar como concluídas as tarefas de hoje" in resposta, f"resposta: {resposta}")
    check("entrou no estado de confirmação em massa", estado == "confirming_bulk_complete", f"estado: {estado}")

    _cleanup()


def test_historico_ignora_registros_sem_created_at():
    print("\n[28] Histórico ignora registros sem created_at")
    _reset_capture()
    _cleanup()

    db = SessionLocal()
    try:
        db.add(ConversationHistory(user_id=TEST_USER, role="user", content="turno antigo nulo 1", created_at=None))
        db.add(ConversationHistory(user_id=TEST_USER, role="assistant", content="turno antigo nulo 2", created_at=None))
        db.commit()
    finally:
        db.close()

    salvar_historico(TEST_USER, "user", "turno recente válido")
    salvar_historico(TEST_USER, "assistant", "resposta recente válida")
    historico = carregar_historico(TEST_USER)
    conteudos = [item["content"] for item in historico]

    check("não carregou linhas sem timestamp", "turno antigo nulo 1" not in conteudos and "turno antigo nulo 2" not in conteudos, f"histórico: {conteudos}")
    check("preservou linhas válidas recentes", conteudos == ["turno recente válido", "resposta recente válida"], f"histórico: {conteudos}")

    db = SessionLocal()
    try:
        recente = db.query(ConversationHistory).filter(
            ConversationHistory.user_id == TEST_USER,
            ConversationHistory.content == "resposta recente válida",
        ).order_by(ConversationHistory.id.desc()).first()
        check("salvar_historico persiste created_at explicitamente", recente is not None and recente.created_at is not None, f"created_at: {getattr(recente, 'created_at', None)}")
    finally:
        db.close()

    _cleanup()


# ─── Runner ───────────────────────────────────────────────────────────────

async def main():
    print("=" * 55)
    print("Sara — Testes de Deploy")
    print("=" * 55)

    try:
        test_db()
        test_keywords()
        test_briefing_format()
        test_buscar_tarefas_hoje()
        await test_planning_com_tarefas()
        await test_planning_sem_tarefas_noite()
        await test_planning_sem_tarefas_manha()
        await test_planning_com_data_explicita()
        await test_toggle_revisao()
        test_estados()
        await test_fluxo_revisao_em_lote()
        await test_revisao_check()
        test_reagendar_backlog_deterministico()
        test_tools_destrutivas_fora_do_schema_geral()
        test_prompt_bloqueia_delecao_no_fluxo_livre()
        test_delete_deterministico_por_titulo()
        test_delete_deterministico_em_massa_por_data()
        test_preempcao_listagem_durante_revisao()
        test_preempcao_backlog_durante_planning()
        test_conclusao_em_massa_do_backlog_sem_pedir_data()
        test_conclusao_parcial_do_backlog_com_selecao()
        test_write_tool_ignora_confirmacao_falsa_do_llm()
        test_revisao_confirma_so_o_que_persistiu()
        test_complete_task_ambiguo_nao_muta()
        test_delete_ambiguo_pede_selecao()
        test_listagem_ignora_categoria_persistida_stale()
        test_conclusao_em_massa_reconhece_minhas_atividades_de_hoje()
        test_historico_ignora_registros_sem_created_at()
    finally:
        _cleanup()

    total = len(_results)
    passed = sum(1 for _, ok in _results if ok)
    failed = total - passed

    print(f"\n{'=' * 55}")
    print(f"Resultado: {passed}/{total} OK" + (f"  —  {failed} FALHA(S)" if failed else ""))
    if failed:
        print("Testes que falharam:")
        for name, ok in _results:
            if not ok:
                print(f"  ✗ {name}")
        print("\nDEPLOY NÃO VALIDADO")
        sys.exit(1)
    else:
        print("DEPLOY VALIDADO ✓")


asyncio.run(main())
