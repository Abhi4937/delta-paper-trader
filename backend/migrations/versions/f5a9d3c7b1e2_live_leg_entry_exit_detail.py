"""live journal: per-leg entry/exit detail (time, fees, margin, mark at exit)

Revision ID: f5a9d3c7b1e2
Revises: e2b7c5a1f3d8
Create Date: 2026-09-24 06:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a9d3c7b1e2'
down_revision: Union[str, Sequence[str], None] = 'e2b7c5a1f3d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('legs', sa.Column('entry_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('legs', sa.Column('entry_fees', sa.Float(), nullable=True))
    op.add_column('legs', sa.Column('entry_margin', sa.Float(), nullable=True))
    op.add_column('legs', sa.Column('last_margin', sa.Float(), nullable=True))
    op.add_column('legs', sa.Column('mark_at_exit', sa.Float(), nullable=True))
    # NOTE: never let autogenerate drop 'strategy_series_time_idx' (TimescaleDB's own index).


def downgrade() -> None:
    for col in ('mark_at_exit', 'last_margin', 'entry_margin', 'entry_fees', 'entry_at'):
        op.drop_column('legs', col)
