import logging
from app.db.database import SessionLocal
from app.models.user_session import UserSession

logger = logging.getLogger(__name__)

VALID_STATES = ("idle", "planning")


def get_session_state(user_id: str) -> str:
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.user_id == user_id).first()
        return session.state if session else "idle"
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
