"""ORM models for the paper-trading platform.

Every user-owned row carries `user_id` so isolation can be enforced on every query
(CLAUDE.md security rule). Money is USD (the engine's base unit). Timestamps are
timezone-aware. The 1-min MTM/IV/greeks history lives in `strategy_series`, made a
TimescaleDB hypertable by the migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    # Supabase user id (sub claim). Null for the dev stub user. Unique when set.
    supabase_uid: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarginCalibration(Base):
    """Self-tuning correction for the local margin fallback: factor ≈ (Delta exact / local),
    EWMA-updated whenever an exact margin is observed, applied when exact is unavailable.
    One row per underlying (the ratio is ~scale-invariant)."""

    __tablename__ = "margin_calibration"

    underlying: Mapped[str] = mapped_column(String(8), primary_key=True)
    factor: Mapped[float] = mapped_column(Float, default=1.0)
    samples: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AllowedEmail(Base):
    """Invite/allowlist: only these emails may log in. Admin-managed."""

    __tablename__ = "allowed_emails"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    invited_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSecret(Base):
    """Per-user Delta credentials, AES-GCM encrypted at rest. Plaintext NEVER leaves
    the server. One row per (user, kind); `kind` is one of the SECRET_KINDS slots."""

    __tablename__ = "user_secrets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Account(Base):
    """One virtual paper account per user (balance is also reconstructable from the ledger)."""

    __tablename__ = "accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    balance_usd: Mapped[float] = mapped_column(Float, nullable=False)
    start_balance_usd: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")  # display preference
    # The builder's unexecuted draft basket (Leg[] as the client stores it), per user, so
    # it follows the user across devices. Opaque JSON — the server never interprets it.
    draft_basket: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True, default=list
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    underlying: Mapped[str] = mapped_column(String(8))
    expiry: Mapped[str] = mapped_column(String(10))  # ISO date
    margin: Mapped[float] = mapped_column(Float)  # current reserved margin (rolls as marks move)
    entry_margin: Mapped[float] = mapped_column(Float)  # margin at open (immutable record)
    margin_badge: Mapped[str] = mapped_column(String(8))  # matched | est | stale
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    # combined net-capital stop
    target_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_pct_of_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    auto_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_exit_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    legs: Mapped[list[Leg]] = relationship(back_populates="position", cascade="all, delete-orphan")
    notes: Mapped[list[Note]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )


class Leg(Base):
    __tablename__ = "legs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(48))
    product_id: Mapped[int] = mapped_column(BigInteger)
    underlying: Mapped[str] = mapped_column(String(8))
    type: Mapped[Literal["call", "put"]] = mapped_column(String(4))
    strike: Mapped[float] = mapped_column(Float)
    contract_value: Mapped[float] = mapped_column(Float)
    expiry: Mapped[str] = mapped_column(String(10))
    dte: Mapped[float] = mapped_column(Float)
    side: Mapped[Literal["buy", "sell"]] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    entry: Mapped[float] = mapped_column(Float)  # fill premium (crosses the spread)
    mark_at_entry: Mapped[float] = mapped_column(Float)
    spot_at_entry: Mapped[float] = mapped_column(Float)
    # per-leg risk
    target_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    auto_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    close_scope: Mapped[Literal["leg", "strategy"]] = mapped_column(String(8), default="leg")
    status: Mapped[Literal["open", "closed"]] = mapped_column(String(8), default="open")
    # exit record (set on close)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exit_gross: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_fees: Mapped[float | None] = mapped_column(Float, nullable=True)

    position: Mapped[Position] = relationship(back_populates="legs")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # deposit | margin_reserve | margin_release | realized | fee
    type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)
    balance_after: Mapped[float] = mapped_column(Float)
    ref: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text)
    tone: Mapped[str | None] = mapped_column(String(8), nullable=True)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))  # entry | exit
    body: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    position: Mapped[Position] = relationship(back_populates="notes")


class StrategySeries(Base):
    """1-min MTM / IV / greeks / book history per strategy (TimescaleDB hypertable on `time`).

    `legs` JSONB holds per-leg {pnl, iv, delta, bid, ask}; spread is ask − bid.
    `atm_iv` JSONB holds ATM mark IV per leg-expiry.
    """

    __tablename__ = "strategy_series"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    pnl: Mapped[float] = mapped_column(Float)
    delta: Mapped[float] = mapped_column(Float)
    theta: Mapped[float] = mapped_column(Float)
    vega: Mapped[float] = mapped_column(Float)
    atm_iv: Mapped[dict[str, float]] = mapped_column(JSONB)  # {expiry: iv}
    # {leg_id: {pnl, iv, delta, bid, ask}}
    legs: Mapped[dict[str, dict[str, float]]] = mapped_column(JSONB)

    __table_args__ = (Index("ix_series_pos_time", "position_id", "time"),)
