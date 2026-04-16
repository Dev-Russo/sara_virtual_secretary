"""add_processed_updates_table

Revision ID: a1b2c3d4e5f6
Revises: 7be109434e7d
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '7be109434e7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'processed_updates',
        sa.Column('update_id', sa.BigInteger(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_processed_updates_created_at', 'processed_updates', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_processed_updates_created_at', table_name='processed_updates')
    op.drop_table('processed_updates')
