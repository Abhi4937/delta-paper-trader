"""live positions: source/exiting + per-leg native stop

Revision ID: a7c3e1d2f9b4
Revises: bf65534914f3
Create Date: 2026-09-23 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3e1d2f9b4'
down_revision: Union[str, Sequence[str], None] = 'bf65534914f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('positions', sa.Column('source', sa.String(5), server_default='paper', nullable=False))
    op.add_column(
        'positions',
        sa.Column('exiting', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column('legs', sa.Column('stop_order_id', sa.BigInteger(), nullable=True))
    op.add_column('legs', sa.Column('stop_price', sa.Float(), nullable=True))
    # NOTE: never let autogenerate drop 'strategy_series_time_idx' (TimescaleDB's own index).


def downgrade() -> None:
    op.drop_column('legs', 'stop_price')
    op.drop_column('legs', 'stop_order_id')
    op.drop_column('positions', 'exiting')
    op.drop_column('positions', 'source')
