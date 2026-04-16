from sqlalchemy import Column, String, Text, DateTime, Enum
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
from app.db.database import Base
import uuid

class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(20), nullable=False)
    title = Column(Text, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)
    priority = Column(String(10), default="medium")
    status = Column(String(15), default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())