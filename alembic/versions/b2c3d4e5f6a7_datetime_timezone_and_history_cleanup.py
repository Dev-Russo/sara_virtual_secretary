"""datetime_timezone_and_history_cleanup

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-16 00:00:00.000000

Alterações:
- Converte todas as colunas DateTime para TIMESTAMPTZ (timezone=True)
- Adiciona índice em conversation_history.created_at para limpeza eficiente
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tasks
    op.alter_column('tasks', 'due_date',
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using='due_date AT TIME ZONE \'UTC\'')
    op.alter_column('tasks', 'created_at',
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using='created_at AT TIME ZONE \'UTC\'')
    op.alter_column('tasks', 'updated_at',
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using='updated_at AT TIME ZONE \'UTC\'')

    # reminders
    op.alter_column('reminders', 'remind_at',
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using='remind_at AT TIME ZONE \'UTC\'')
    op.alter_column('reminders', 'created_at',
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using='created_at AT TIME ZONE \'UTC\'')

    # conversation_history
    op.alter_column('conversation_history', 'created_at',
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using='created_at AT TIME ZONE \'UTC\'')
    op.create_index('ix_conversation_history_created_at', 'conversation_history', ['created_at'])

    # tool_call_logs
    op.alter_column('tool_call_logs', 'created_at',
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using='created_at AT TIME ZONE \'UTC\'')


def downgrade() -> None:
    op.drop_index('ix_conversation_history_created_at', table_name='conversation_history')

    for table, col in [
        ('tasks', 'due_date'), ('tasks', 'created_at'), ('tasks', 'updated_at'),
        ('reminders', 'remind_at'), ('reminders', 'created_at'),
        ('conversation_history', 'created_at'),
        ('tool_call_logs', 'created_at'),
    ]:
        op.alter_column(table, col, type_=sa.DateTime(), existing_nullable=True)
