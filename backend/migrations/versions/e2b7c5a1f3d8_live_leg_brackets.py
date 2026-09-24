"""live brackets: leg SL/target trigger prices, target order id, basket target %

Revision ID: e2b7c5a1f3d8
Revises: c4e8b2a9d6f1
Create Date: 2026-09-24 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2b7c5a1f3d8'
down_revision: Union[str, Sequence[str], None] = 'c4e8b2a9d6f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('legs', sa.Column('sl_price', sa.Float(), nullable=True))
    op.add_column('legs', sa.Column('tp_price', sa.Float(), nullable=True))
    op.add_column('legs', sa.Column('tp_order_id', sa.BigInteger(), nullable=True))
    op.add_column('positions', sa.Column('target_pct_of_margin', sa.Float(), nullable=True))
    # NOTE: never let autogenerate drop 'strategy_series_time_idx' (TimescaleDB's own index).


def downgrade() -> None:
    op.drop_column('positions', 'target_pct_of_margin')
    op.drop_column('legs', 'tp_order_id')
    op.drop_column('legs', 'tp_price')
    op.drop_column('legs', 'sl_price')
