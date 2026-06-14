"""strategy_series mtm ohlc

Revision ID: bf65534914f3
Revises: 1ec38ced982c
Create Date: 2026-06-14 16:14:59.958134

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf65534914f3'
down_revision: Union[str, Sequence[str], None] = '1ec38ced982c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("strategy_series", sa.Column("pnl_open", sa.Float(), nullable=True))
    op.add_column("strategy_series", sa.Column("pnl_high", sa.Float(), nullable=True))
    op.add_column("strategy_series", sa.Column("pnl_low", sa.Float(), nullable=True))
    # No historical sub-10s data exists → seed OHLC = pnl (flat candles for old rows).
    op.execute("UPDATE strategy_series SET pnl_open = pnl, pnl_high = pnl, pnl_low = pnl")


def downgrade() -> None:
    op.drop_column("strategy_series", "pnl_low")
    op.drop_column("strategy_series", "pnl_high")
    op.drop_column("strategy_series", "pnl_open")
