"""add_task_category

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timedelta
import pytz


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TIMEZONE = pytz.timezone("America/Sao_Paulo")
LOGICAL_DAY_CUTOFF_HOUR = 4


def _intervalo_dia_logico(agora: datetime | None = None) -> tuple[datetime, datetime]:
    if agora is None:
        agora = datetime.now(TIMEZONE)
    ref = agora - timedelta(days=1) if agora.hour < LOGICAL_DAY_CUTOFF_HOUR else agora
    inicio = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = ref.replace(hour=23, minute=59, second=59, microsecond=0)
    return inicio, fim


def upgrade() -> None:
    op.add_column("tasks", sa.Column("category", sa.String(length=20), nullable=True))

    inicio, fim = _intervalo_dia_logico()
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET category = 'backlog'
            WHERE status = 'pending' AND due_date IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET category = 'overdue'
            WHERE status = 'pending' AND due_date IS NOT NULL AND due_date < :inicio
            """
        ).bindparams(inicio=inicio)
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET category = 'today'
            WHERE status = 'pending' AND due_date >= :inicio AND due_date <= :fim
            """
        ).bindparams(inicio=inicio, fim=fim)
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET category = 'upcoming'
            WHERE status = 'pending' AND due_date > :fim
            """
        ).bindparams(fim=fim)
    )


def downgrade() -> None:
    op.drop_column("tasks", "category")
