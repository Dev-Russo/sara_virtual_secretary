from sqlalchemy import Column, String, DateTime
from sqlalchemy.sql import func
from app.db.database import Base


class UserSession(Base):
    __tablename__ = "user_sessions"

    user_id = Column(String(20), primary_key=True)
    state = Column(String(20), nullable=False, default="idle")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
