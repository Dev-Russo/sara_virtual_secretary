"""Seed data for local harness runs."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from app.agent.session import set_session_state
from app.db.database import SessionLocal
from app.models.reminder import Reminder
from app.models.task import Task

from tests.harness.db import (
    CORE_USER_ID,
    VOLUME_USER_ID,
    assert_dev_database,
    reset_user_data,
)

TIMEZONE = pytz.timezone("America/Sao_Paulo")


def _default_now() -> datetime:
    return datetime.now(TIMEZONE).replace(second=0, microsecond=0)


def seed_core(now: datetime | None = None) -> dict:
    assert_dev_database()
    reset_user_data(CORE_USER_ID)
    now = now or _default_now()
    today = now.replace(hour=10, minute=0)
    yesterday = today - timedelta(days=1)

    db = SessionLocal()
    try:
        tasks = [
            Task(user_id=CORE_USER_ID, title="Enviar relatório diário", due_date=today),
            Task(user_id=CORE_USER_ID, title="Responder orçamento antigo", due_date=yesterday),
            Task(user_id=CORE_USER_ID, title="Revisar arquitetura da Sara", due_date=None),
            Task(user_id=CORE_USER_ID, title="Estudar docker", due_date=None),
            Task(user_id=CORE_USER_ID, title="Tarefa já concluída", status="done"),
        ]
        db.add_all(tasks)
        db.flush()

        reminder = Reminder(
            user_id=CORE_USER_ID,
            message="Lembrete vencido de teste",
            remind_at=now - timedelta(hours=1),
            sent=False,
        )
        db.add(reminder)
        db.commit()

        task_ids = []
        for task in tasks:
            db.refresh(task)
            task_ids.append(str(task.id))
        db.refresh(reminder)
    finally:
        db.close()

    set_session_state(CORE_USER_ID, "idle", context={}, replace_context=True)

    return {
        "user_id": CORE_USER_ID,
        "tasks": task_ids,
        "reminders": [str(reminder.id)],
    }


def seed_volume(count: int = 500, now: datetime | None = None) -> dict:
    assert_dev_database()
    reset_user_data(VOLUME_USER_ID)
    now = now or _default_now()
    today = now.replace(hour=9, minute=0)
    dates = [None, today - timedelta(days=1), today, today + timedelta(days=1)]

    db = SessionLocal()
    try:
        tasks = [
            Task(
                user_id=VOLUME_USER_ID,
                title=f"Tarefa volume {i:04d}",
                due_date=dates[i % len(dates)],
            )
            for i in range(count)
        ]
        db.add_all(tasks)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {"user_id": VOLUME_USER_ID, "task_count": count}

