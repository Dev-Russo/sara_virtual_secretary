from sqlalchemy import Column, String, Text, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from app.db.database import Base
from datetime import datetime
import uuid


class ToolCallLog(Base):
    """
    Registro de auditoria de todas as chamadas de tools pelo LLM.
    Permite rastrear decisões, validar argumentos e debugar problemas.
    """
    __tablename__ = "tool_call_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(20), nullable=False)
    tool_name = Column(String(50), nullable=False)
    arguments = Column(JSON, nullable=True)
    result = Column(Text, nullable=True)
    llm_response = Column(Text, nullable=True)
    validation_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
