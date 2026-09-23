"""Live trade journal: one snapshot per live group — entry/exit, every leg, realised P&L,
max/min MTM (with times), max drawdown, duration. Stored on the position when it closes
(so it survives any later history retention); computed on the fly while it is open.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Position, StrategySeries

Sample = tuple[int, float, float, float, float]  # (t_ms, open, high, low, close) of net MTM


def _ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


def mtm_stats(samples: list[Sample]) -> dict[str, Any]:
    """Max/min MTM with their times, and max drawdown (largest fall from a running peak).

    Within one 10s bar the order of its high and low is unknown, so a bar's low is measured
    against the peak reached BEFORE it (and the bar's open) — never against its own high.
    """
    if not samples:
        return {
            "maxMtm": None,
            "maxMtmAt": None,
            "minMtm": None,
            "minMtmAt": None,
            "maxDrawdown": 0.0,
            "drawdownPeakAt": None,
            "drawdownTroughAt": None,
        }
    hi = max(samples, key=lambda s: s[2])
    lo = min(samples, key=lambda s: s[3])
    peak, peak_at = samples[0][1], samples[0][0]
    dd, dd_peak_at, dd_trough_at = 0.0, None, None
    for t, o, h, low, _c in samples:
        if o > peak:
            peak, peak_at = o, t
        if peak - low > dd:
            dd, dd_peak_at, dd_trough_at = peak - low, peak_at, t
        if h > peak:
            peak, peak_at = h, t
    return {
        "maxMtm": hi[2],
        "maxMtmAt": hi[0],
        "minMtm": lo[3],
        "minMtmAt": lo[0],
        "maxDrawdown": dd,
        "drawdownPeakAt": dd_peak_at,
        "drawdownTroughAt": dd_trough_at,
    }


def summarize(pos: Position, samples: list[Sample], now: datetime) -> dict[str, Any]:
    closed = pos.status == "closed"
    end = pos.closed_at if closed and pos.closed_at else now
    legs = []
    realized = fees = 0.0
    for lg in pos.legs:
        if lg.status == "closed":
            gross, fee = lg.exit_gross or 0.0, lg.exit_fees or 0.0
            realized += gross - fee
            fees += fee
        legs.append(
            {
                "symbol": lg.symbol,
                "side": lg.side,
                "qty": lg.qty,
                "type": lg.type,
                "strike": lg.strike,
                "expiry": lg.expiry,
                "entry": lg.entry,
                "exit": lg.exit_price,
                "exitAt": _ms(lg.exit_at),
                "exitReason": lg.exit_reason,
                "pnl": (lg.exit_gross or 0.0) - (lg.exit_fees or 0.0)
                if lg.status == "closed"
                else None,
                "fees": lg.exit_fees,
                "stopLoss": lg.stop_pnl,
                "deltaStopPrice": lg.stop_price,
                "status": lg.status,
            }
        )
    return {
        "id": str(pos.id),
        "name": pos.name,
        "underlying": pos.underlying,
        "expiry": pos.expiry,
        "status": pos.status,
        "openedAt": _ms(pos.opened_at),
        "closedAt": _ms(pos.closed_at),
        "durationSeconds": int((end - pos.opened_at).total_seconds()),
        "closeReason": pos.close_reason,
        "armed": pos.auto_exit,
        "basketStopLoss": pos.stop_loss_amount,
        "basketStopLossPctOfMargin": pos.stop_loss_pct_of_margin,
        # final P&L: realised on close; for an open trade the last recorded MTM
        "pnl": realized if closed else (samples[-1][4] if samples else None),
        "fees": fees,
        "legs": legs,
        **mtm_stats(samples),
    }


async def load_samples(
    session: AsyncSession, pos: Position, ring: Iterable[dict[str, Any]] | None
) -> list[Sample]:
    """Every durable 10s MTM bar for the position, plus any newer 1s ring samples."""
    rows = await session.execute(
        select(
            StrategySeries.time,
            StrategySeries.pnl,
            StrategySeries.pnl_open,
            StrategySeries.pnl_high,
            StrategySeries.pnl_low,
        )
        .where(StrategySeries.position_id == pos.id)
        .order_by(StrategySeries.time)
    )
    out: list[Sample] = []
    for t, c, o, h, low in rows.all():
        out.append(
            (
                _ms(t) or 0,
                o if o is not None else c,
                h if h is not None else c,
                low if low is not None else c,
                c,
            )
        )
    last = out[-1][0] if out else 0
    for s in ring or []:
        if s["t"] > last:
            p = s["pnl"]
            out.append((s["t"], p, p, p, p))
    return out


async def trade_summary(
    session: AsyncSession, pos: Position, ring: Iterable[dict[str, Any]] | None
) -> dict[str, Any]:
    if pos.status == "closed" and pos.summary:
        return dict(pos.summary)
    return summarize(pos, await load_samples(session, pos, ring), datetime.now(UTC))
