"""Database safety and reset helpers for the development harness."""

from __future__ import annotations

import os

from app.config import DATABASE_URL
from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from app.models.reminder import Reminder
from app.models.task import Task
from app.models.tool_call_log import ToolCallLog
from app.models.user_session import UserSession

CORE_USER_ID = "dev_core_user"
VOLUME_USER_ID = "dev_volume_user"


def _override_enabled() -> bool:
    return os.getenv("ALLOW_DEV_DB_RESET") == "1"


def assert_dev_database() -> None:
    """Fail closed unless the configured database is clearly local/dev."""
    env_file = os.getenv("ENV_FILE", "")
    database_url = DATABASE_URL or ""

    if _override_enabled():
        return

    if not env_file.endswith(".env.local"):
        raise RuntimeError(
            "Refusing dev DB operation: ENV_FILE must end with .env.local "
            "or ALLOW_DEV_DB_RESET=1 must be set."
        )

    lowered_url = database_url.lower()
    if not database_url:
        raise RuntimeError("Refusing dev DB operation: DATABASE_URL is empty.")
    if "oracle" in lowered_url or "sarasecretary.duckdns.org" in lowered_url:
        raise RuntimeError("Refusing dev DB operation: DATABASE_URL looks production-like.")
    if "localhost" not in lowered_url and "127.0.0.1" not in lowered_url:
        raise RuntimeError("Refusing dev DB operation: DATABASE_URL is not local.")


def reset_user_data(user_id: str) -> None:
    """Delete harness-owned data for one user from local development storage."""
    assert_dev_database()
    db = SessionLocal()
    try:
        db.query(Reminder).filter(Reminder.user_id == user_id).delete()
        db.query(Task).filter(Task.user_id == user_id).delete()
        db.query(UserSession).filter(UserSession.user_id == user_id).delete()
        db.query(ConversationHistory).filter(ConversationHistory.user_id == user_id).delete()
        db.query(ToolCallLog).filter(ToolCallLog.user_id == user_id).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_seed_data() -> None:
    reset_user_data(CORE_USER_ID)
    reset_user_data(VOLUME_USER_ID)

