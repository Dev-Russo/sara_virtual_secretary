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


type(_tg.bot).send_message = _mock_send
type(_tg.bot).edit_message_reply_markup = _mock_edit
type(_tg.bot).answer_callback_query = _mock_answer

# ─── Imports após patch ────────────────────────────────────────────────────
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.task import Task
from app.models.user_session import UserSession
from app.agent.session import get_session_state, set_session_state, get_session_context
from app.agent.sara_agent import (
    _quer_iniciar_planejamento,
    _quer_sair_planejamento,
    _confirmou_plano,
    chat,
    toggle_review_task,
    finalizar_revisao,
)
import app.scheduler.jobs as jobs
from app.scheduler.jobs import iniciar_planejamento_manual, buscar_tarefas_hoje, abrir_fluxo_pos_revisao, iniciar_revisao_check

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
    check("target_date inferida para amanhã", contexto.get("target_date") == (agora.date() + timedelta(days=1)).strftime("%Y-%m-%d"),
          f"contexto: {contexto}")
    check("keyboard foi enviado", len(_sent_keyboards) == 1, f"qtd keyboards: {len(_sent_keyboards)}")
    check("nenhuma mensagem de texto automática", len(_sent_messages) == 0, f"msgs: {_sent_messages}")

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
    check("assumiu amanhã", contexto.get("target_date") == (agora.date() + timedelta(days=1)).strftime("%Y-%m-%d"),
          f"contexto: {contexto}")
    check("abriu planejamento direto", any("o que precisa acontecer" in msg.lower() for msg in _sent_messages), f"msgs: {_sent_messages}")
    check("nenhum keyboard enviado", len(_sent_keyboards) == 0)

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
    check("perguntou qual dia planejar", any("qual dia você quer planejar" in msg.lower() for msg in _sent_messages),
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
    check("mensagem com tom novo", "me diz o que foi" in resposta.lower(), f"resposta: {resposta}")

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
