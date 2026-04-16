from sqlalchemy import Column, BigInteger, DateTime
from sqlalchemy.sql import func
from app.db.database import Base


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"

    update_id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())
