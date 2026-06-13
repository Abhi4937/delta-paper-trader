"""position stale_hard_stop

Revision ID: 1ec38ced982c
Revises: f286e3cd79c0
Create Date: 2026-06-14 03:14:28.706407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ec38ced982c'
down_revision: Union[str, Sequence[str], None] = 'f286e3cd79c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'positions',
        sa.Column('stale_hard_stop', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    # NOTE: 'strategy_series_time_idx' is TimescaleDB's auto hypertable index — NOT ours;
    # autogenerate keeps wanting to drop it. Do not. (Same caveat as prior migrations.)


def downgrade() -> None:
    op.drop_column('positions', 'stale_hard_stop')
