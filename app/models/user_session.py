from sqlalchemy import Column, String, DateTime, JSON
from sqlalchemy.sql import func

from app.agent.contracts import STATE_IDLE
from app.db.database import Base


class UserSession(Base):
    __tablename__ = "user_sessions"

    user_id = Column(String(20), primary_key=True)
    state = Column(String(50), nullable=False, default=STATE_IDLE)
    context = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
