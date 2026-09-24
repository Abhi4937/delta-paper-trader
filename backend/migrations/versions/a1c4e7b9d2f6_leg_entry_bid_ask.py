"""legs: best bid / ask at entry

Revision ID: a1c4e7b9d2f6
Revises: f5a9d3c7b1e2
Create Date: 2026-09-25 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c4e7b9d2f6'
down_revision: Union[str, Sequence[str], None] = 'f5a9d3c7b1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('legs', sa.Column('entry_bid', sa.Float(), nullable=True))
    op.add_column('legs', sa.Column('entry_ask', sa.Float(), nullable=True))
    # NOTE: never let autogenerate drop 'strategy_series_time_idx' (TimescaleDB's own index).


def downgrade() -> None:
    op.drop_column('legs', 'entry_ask')
    op.drop_column('legs', 'entry_bid')
