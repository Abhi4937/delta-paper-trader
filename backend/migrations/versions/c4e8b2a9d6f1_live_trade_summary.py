"""live trade journal: positions.summary snapshot

Revision ID: c4e8b2a9d6f1
Revises: a7c3e1d2f9b4
Create Date: 2026-09-23 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c4e8b2a9d6f1'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1d2f9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('positions', sa.Column('summary', postgresql.JSONB(), nullable=True))
    # NOTE: never let autogenerate drop 'strategy_series_time_idx' (TimescaleDB's own index).


def downgrade() -> None:
    op.drop_column('positions', 'summary')
