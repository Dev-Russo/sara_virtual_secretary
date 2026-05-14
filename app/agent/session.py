import logging
from datetime import datetime, timezone

from app.db.database import SessionLocal
from app.models.user_session import UserSession

logger = logging.getLogger(__name__)

VALID_STATES = (
    "idle",
    "adding_task",
    "planning",
    "reviewing_tasks",
    "reviewing_pending_tasks",
    "review_confirming",
    "confirming_single_complete",
    "confirming_bulk_complete",
    "confirming_backlog_review",
    "confirming_reschedule_backlog",
    "confirming_delete",
)

# Estados de planejamento expiram após inatividade — evita usuário preso
STATE_TTL_MINUTES = 180  # 3 horas


def get_session_state(user_id: str) -> str:
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.user_id == user_id).first()
        if not session:
            return "idle"

        if session.state in (
            "adding_task",
            "planning",
            "reviewing_tasks",
            "reviewing_pending_tasks",
            "review_confirming",
            "confirming_single_complete",
            "confirming_bulk_complete",
            "confirming_backlog_review",
            "confirming_reschedule_backlog",
            "confirming_delete",
        ) and session.updated_at:
            updated = session.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            idade_min = (datetime.now(timezone.utc) - updated).total_seconds() / 60
            if idade_min > STATE_TTL_MINUTES:
                logger.info(
                    f"[Session] {user_id} estado '{session.state}' expirou "
                    f"({idade_min:.0f}min), resetando para idle"
                )
                session.state = "idle"
                session.context = {}
                db.commit()
                return "idle"

        return session.state
    except Exception as e:
        logger.error(f"[get_session_state] {e}")
        return "idle"
    finally:
        db.close()


def get_session_context(user_id: str) -> dict:
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.user_id == user_id).first()
        if not session or not isinstance(session.context, dict):
            return {}
        return session.context
    except Exception as e:
        logger.error(f"[get_session_context] {e}")
        return {}
    finally:
        db.close()


def set_session_state(
    user_id: str,
    state: str,
    *,
    context: dict | None = None,
    replace_context: bool = False,
) -> None:
    if state not in VALID_STATES:
        logger.warning(f"[set_session_state] Estado inválido: {state}")
        return
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.user_id == user_id).first()
        if session:
            session.state = state
            atual = session.context if isinstance(session.context, dict) else {}
            if context is not None:
                session.context = dict(context) if replace_context else {**atual, **context}
            elif state == "idle":
                session.context = {}
        else:
            session = UserSession(
                user_id=user_id,
                state=state,
                context=dict(context or {}),
            )
            db.add(session)
        db.commit()
        logger.info(f"[Session] {user_id} → {state}")
    except Exception as e:
        logger.error(f"[set_session_state] {e}")
        db.rollback()
    finally:
        db.close()


def update_session_context(user_id: str, updates: dict, *, clear: bool = False) -> None:
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.user_id == user_id).first()
        if not session:
            session = UserSession(user_id=user_id, state="idle", context={})
            db.add(session)
        atual = {} if clear or not isinstance(session.context, dict) else dict(session.context)
        atual.update(updates)
        session.context = atual
        db.commit()
    except Exception as e:
        logger.error(f"[update_session_context] {e}")
        db.rollback()
    finally:
        db.close()
