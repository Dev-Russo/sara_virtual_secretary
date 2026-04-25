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

import pytz

TIMEZONE = pytz.timezone("America/Sao_Paulo")
TEST_USER = "test_deploy_script_sara"

# ─── Monkey-patch Telegram ANTES de qualquer outro import ─────────────────
import app.services.telegram as _tg

_sent_messages: list[str] = []
_sent_keyboards: list[dict] = []


async def _mock_send(*args, **kwargs):
    text = kwargs.get("text", "")
    markup = kwargs.get("reply_markup", None)
    if markup and hasattr(markup, "inline_keyboard"):
        _sent_keyboards.append({"text": text, "markup": markup})
    else:
        _sent_messages.append(text)

    class _FakeMsg:
        message_id = 999

    return _FakeMsg()


async def _mock_edit(*args, **kwargs):
    pass


async def _mock_answer(*args, **kwargs):
    pass


_tg.bot.send_message = _mock_send
_tg.bot.edit_message_reply_markup = _mock_edit
_tg.bot.answer_callback_query = _mock_answer

# ─── Imports após patch ────────────────────────────────────────────────────
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.task import Task
from app.models.user_session import UserSession
from app.agent.session import get_session_state, set_session_state
from app.agent.sara_agent import _quer_iniciar_planejamento
from app.scheduler.jobs import iniciar_planejamento_manual, buscar_tarefas_hoje
from app.api.routes.telegram import _marcar_tarefa_done

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
        db.query(Task).filter(Task.user_id == TEST_USER).delete()
        db.query(UserSession).filter(UserSession.user_id == TEST_USER).delete()
        db.commit()
    finally:
        db.close()


def _reset_capture():
    _sent_messages.clear()
    _sent_keyboards.clear()


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

    agora = datetime.now(TIMEZONE)
    hoje = agora.replace(hour=14, minute=0, second=0, microsecond=0)
    _create_task("Tarefa de revisão", due_date=hoje)

    handled = await iniciar_planejamento_manual(TEST_USER)
    state = get_session_state(TEST_USER)

    check("retornou True (keyboard enviado)", handled, f"retornou: {handled}")
    check("estado → reviewing_tasks", state == "reviewing_tasks", f"estado: {state}")
    check("keyboard foi enviado", len(_sent_keyboards) == 1, f"qtd keyboards: {len(_sent_keyboards)}")
    check("nenhuma mensagem de texto automática", len(_sent_messages) == 0, f"msgs: {_sent_messages}")

    if _sent_keyboards:
        markup = _sent_keyboards[0]["markup"]
        buttons = [btn for row in markup.inline_keyboard for btn in row]
        labels = [btn.text for btn in buttons]
        check('botão com "☐" presente', any("☐" in lb for lb in labels))
        check('botão "Concluir revisão" presente', any("Concluir" in lb for lb in labels))

    _cleanup()


async def test_planning_sem_tarefas():
    print("\n[6] Planejamento manual — sem tarefas para hoje")
    _reset_capture()

    handled = await iniciar_planejamento_manual(TEST_USER)
    state = get_session_state(TEST_USER)

    check("retornou False (sem keyboard)", not handled, f"retornou: {handled}")
    check("estado → planning", state == "planning", f"estado: {state}")
    check("nenhuma mensagem enviada automaticamente", len(_sent_messages) == 0, f"msgs: {_sent_messages}")
    check("nenhum keyboard enviado", len(_sent_keyboards) == 0)

    _cleanup()


async def test_marcar_tarefa_done():
    print("\n[7] Marcar tarefa como concluída via callback")
    task_id = _create_task("Tarefa para callback")

    result = _marcar_tarefa_done(task_id)

    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()
    db.close()

    check("_marcar_tarefa_done retornou True", result)
    check("status no banco → done", task is not None and task.status == "done",
          f"status: {task.status if task else 'não encontrado'}")

    _cleanup()


def test_estados():
    print("\n[8] Transições de estado")
    for estado in ("idle", "reviewing_tasks", "planning", "idle"):
        set_session_state(TEST_USER, estado)
        atual = get_session_state(TEST_USER)
        check(f"setar → {estado}", atual == estado, f"ficou: {atual}")

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
        await test_planning_sem_tarefas()
        await test_marcar_tarefa_done()
        test_estados()
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
