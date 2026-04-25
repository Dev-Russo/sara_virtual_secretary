import logging
from datetime import datetime, timezone

from app.db.database import SessionLocal
from app.models.user_session import UserSession

logger = logging.getLogger(__name__)

VALID_STATES = ("idle", "planning", "reviewing_tasks")

# Estados de planejamento expiram após inatividade — evita usuário preso
STATE_TTL_MINUTES = 180  # 3 horas


def get_session_state(user_id: str) -> str:
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.user_id == user_id).first()
        if not session:
            return "idle"

        if session.state in ("planning", "reviewing_tasks") and session.updated_at:
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
                db.commit()
                return "idle"

        return session.state
    except Exception as e:
        logger.error(f"[get_session_state] {e}")
        return "idle"
    finally:
        db.close()


def set_session_state(user_id: str, state: str) -> None:
    if state not in VALID_STATES:
        logger.warning(f"[set_session_state] Estado inválido: {state}")
        return
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.user_id == user_id).first()
        if session:
            session.state = state
        else:
            session = UserSession(user_id=user_id, state=state)
            db.add(session)
        db.commit()
        logger.info(f"[Session] {user_id} → {state}")
    except Exception as e:
        logger.error(f"[set_session_state] {e}")
        db.rollback()
    finally:
        db.close()
