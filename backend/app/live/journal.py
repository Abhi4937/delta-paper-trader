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
CANDLES_PER_CALL = 3000  # Delta returns at most 4000 1m candles per request


# --- fills: entry / exit detail --------------------------------------------- #
def parse_ts(v: Any) -> datetime | None:
    """Delta timestamps: ISO string or epoch microseconds."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        return datetime.fromtimestamp(int(v) / 1_000_000, UTC)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def fill_run(fills: list[dict[str, Any]], product_id: int, side: str) -> list[dict[str, Any]]:
    """Newest-first consecutive fills on one product and side: the fills that built (or
    closed) the position, stopping at the first fill on the other side."""
    run = []
    for f in fills:
        if int(f.get("product_id") or 0) != product_id:
            continue
        if f.get("side") != side:
            break
        run.append(f)
    return run


def fill_summary(run: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Size-weighted price, total size and commissions, first/last fill time."""
    if not run:
        return None
    size = sum(abs(float(f.get("size") or 0)) for f in run)
    price = (
        sum(abs(float(f.get("size") or 0)) * float(f.get("price") or 0) for f in run) / size
        if size
        else float(run[0].get("price") or 0)
    )
    times = [t for t in (parse_ts(f.get("created_at")) for f in run) if t is not None]
    return {
        "price": price,
        "size": size,
        "fees": sum(float(f.get("commission") or 0) for f in run),
        "first_at": min(times) if times else None,
        "last_at": max(times) if times else None,
    }


def slippage(
    side: str, fill: float | None, mark: float | None, qty: float, cv: float
) -> float | None:
    """USD cost of the fill vs the mark at that moment (positive = paid, negative = better
    than mark). `side` is the side of THIS fill: buy above mark / sell below mark costs."""
    if fill is None or mark is None:
        return None
    per_unit = (fill - mark) if side == "buy" else (mark - fill)
    return per_unit * qty * cv


# --- 1-minute mark candles per leg ------------------------------------------ #
def candle_rows(
    side: str, entry: float, qty: float, cv: float, candles: list[list[float]]
) -> list[list[float]]:
    """[t, o, h, l, c] mark candles -> + leg P&L o/h/l/c. For a sold leg the premium's
    high is the P&L's low."""
    sign = 1 if side == "buy" else -1

    def pnl(px: float) -> float:
        return sign * (px - entry) * qty * cv

    rows = []
    for t, o, h, lo, c in candles:
        hi_p, lo_p = (pnl(h), pnl(lo)) if sign > 0 else (pnl(lo), pnl(h))
        rows.append([t, o, h, lo, c, pnl(o), hi_p, lo_p, pnl(c)])
    return rows


async def fetch_candles(
    http: Any, base: str, symbol: str, start: datetime, end: datetime
) -> list[list[float]]:
    """1m MARK-price candles from Delta's public history, oldest first: [t_ms, o, h, l, c].
    Paged backwards (Delta caps each call). Empty on any failure — never blocks a close."""
    out: dict[int, list[float]] = {}
    lo_s, hi_s = int(start.timestamp()) - 60, int(end.timestamp()) + 60
    cur = hi_s
    try:
        while cur > lo_s:
            frm = max(lo_s, cur - CANDLES_PER_CALL * 60)
            r = await http.get(
                f"{base}/v2/history/candles",
                params={"resolution": "1m", "symbol": f"MARK:{symbol}", "start": frm, "end": cur},
                timeout=20.0,
            )
            for k in r.json().get("result") or []:
                t = int(k["time"])
                out[t] = [
                    t * 1000,
                    float(k["open"]),
                    float(k["high"]),
                    float(k["low"]),
                    float(k["close"]),
                ]
            cur = frm
    except Exception:  # noqa: BLE001
        return [out[t] for t in sorted(out)]
    return [out[t] for t in sorted(out)]


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
        # brokerage on both sides (Delta charges entry AND exit commission)
        leg_fees = (lg.entry_fees or 0.0) + (lg.exit_fees or 0.0 if lg.status == "closed" else 0.0)
        fees += leg_fees
        if lg.status == "closed":
            realized += (lg.exit_gross or 0.0) - leg_fees
        exit_side = "buy" if lg.side == "sell" else "sell"
        legs.append(
            {
                "entryAt": _ms(lg.entry_at),
                "markAtEntry": lg.mark_at_entry,
                "entrySlippage": slippage(
                    lg.side, lg.entry, lg.mark_at_entry, lg.qty, lg.contract_value
                ),
                "entryFees": lg.entry_fees,
                "entryMargin": lg.entry_margin,
                "markAtExit": lg.mark_at_exit,
                "exitSlippage": slippage(
                    exit_side, lg.exit_price, lg.mark_at_exit, lg.qty, lg.contract_value
                ),
                "exitMargin": lg.last_margin,
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
                "pnl": (lg.exit_gross or 0.0) - (lg.entry_fees or 0.0) - (lg.exit_fees or 0.0)
                if lg.status == "closed"
                else None,
                "grossPnl": lg.exit_gross if lg.status == "closed" else None,
                "fees": lg.exit_fees,  # exit brokerage (entry brokerage is entryFees)
                "slPrice": lg.sl_price,
                "tpPrice": lg.tp_price,
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
        "basketTarget": pos.target_pnl,
        "basketTargetPctOfMargin": pos.target_pct_of_margin,
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
