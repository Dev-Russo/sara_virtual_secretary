"""add_tool_call_logs_table

Revision ID: 7be109434e7d
Revises: fb42a07b1d29
Create Date: 2026-04-12 12:27:28.108360

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7be109434e7d'
down_revision: Union[str, Sequence[str], None] = 'fb42a07b1d29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create tool_call_logs table for audit trail."""
    op.create_table(
        'tool_call_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.String(20), nullable=False),
        sa.Column('tool_name', sa.String(50), nullable=False),
        sa.Column('arguments', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('llm_response', sa.Text(), nullable=True),
        sa.Column('validation_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # Index for common queries
    op.create_index('ix_tool_call_logs_user_id', 'tool_call_logs', ['user_id'])
    op.create_index('ix_tool_call_logs_tool_name', 'tool_call_logs', ['tool_name'])
    op.create_index('ix_tool_call_logs_created_at', 'tool_call_logs', ['created_at'])


def downgrade() -> None:
    """Drop tool_call_logs table."""
    op.drop_index('ix_tool_call_logs_created_at', table_name='tool_call_logs')
    op.drop_index('ix_tool_call_logs_tool_name', table_name='tool_call_logs')
    op.drop_index('ix_tool_call_logs_user_id', table_name='tool_call_logs')
    op.drop_table('tool_call_logs')
