"""Server-authoritative paper-sim service: place / close / leg-close / risk / note,
plus live-MTM sampling and full-state assembly. All money math is ported from the
validated client engine (engines/money.py). Every query is filtered by user_id.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.vault import get_secret
from app.db.models import Account, LedgerEntry, Leg, Log, Note, Position, StrategySeries
from app.engines.margin import bs_greeks
from app.engines.money import (
    LegGreeks,
    LegQuote,
    entry_fill,
    exit_fill,
    leg_entry_slippage,
    leg_fee,
    leg_pnl,
    leg_sign,
    net_greeks,
    net_pnl,
    spread,
)
from app.services.chain import is_expiry_live
from app.services.market_data import UNDERLYINGS
from app.sim.exit_engine import CombinedStop, ExitLeg, evaluate_exit
from app.sim.margin_helper import quote_margin
from app.sim.marketview import MarketView, Quote

# How much of the live 1s ring to return in GET /api/state. Everything before this
# tail is served from the durable 10s DB history, so the chart starts at entry with a
# bounded payload no matter how long the position has been open. 5h = 18,000 samples.
TAIL_1S = 5 * 60 * 60


class MtmOhlc:
    """Running open/high/low/close of net MTM across one 10s DB-write window."""

    __slots__ = ("open", "high", "low", "close", "_started")

    def __init__(self) -> None:
        self._started = False

    def add(self, v: float) -> None:
        if not self._started:
            self.open = self.high = self.low = self.close = v
            self._started = True
        else:
            self.high = max(self.high, v)
            self.low = min(self.low, v)
            self.close = v

    def started(self) -> bool:
        return self._started

    def snapshot(self) -> dict[str, float]:
        return {"open": self.open, "high": self.high, "low": self.low, "close": self.close}

    def reset(self) -> None:
        self._started = False


def _ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


async def _account(session: AsyncSession, user_id: uuid.UUID) -> Account:
    res = await session.execute(select(Account).where(Account.user_id == user_id))
    return res.scalar_one()


async def _get_position(
    session: AsyncSession, user_id: uuid.UUID, pos_id: uuid.UUID
) -> Position | None:
    res = await session.execute(
        select(Position)
        .where(Position.id == pos_id, Position.user_id == user_id)
        .options(selectinload(Position.legs), selectinload(Position.notes))
    )
    return res.scalar_one_or_none()


# --- live valuation --------------------------------------------------------- #
def _open(pos: Position) -> list[Leg]:
    return [lg for lg in pos.legs if lg.status == "open"]


def _mark(mv: MarketView, leg: Leg) -> float:
    q = mv.quote(leg.symbol)
    return q.mark if (q and q.mark) else leg.entry


def position_pnl(pos: Position, mv: MarketView) -> float:
    legs = [
        LegQuote(lg.side, lg.qty, lg.contract_value, lg.entry, _mark(mv, lg)) for lg in _open(pos)
    ]
    return net_pnl(legs)


def leg_greeks(lg: Leg, q: Quote | None, spot: float) -> tuple[float, float, float]:
    """Per-contract (delta, theta, vega) for a leg: from the feed when it provided
    greeks, else a local Black-Scholes fallback (so risk panels don't read zeros when
    the feed omits greeks for a symbol). Falls back only with spot + iv + positive dte."""
    if q is None:
        return 0.0, 0.0, 0.0
    if q.greeks_present:
        return q.delta, q.theta, q.vega
    if q.iv > 0 and spot > 0 and lg.dte > 0:
        g = bs_greeks(lg.type, spot, lg.strike, lg.dte / 365.0, q.iv)
        return g["delta"], g["theta"], g["vega"]
    return q.delta, q.theta, q.vega


def build_sample(pos: Position, mv: MarketView, now: datetime) -> dict[str, Any]:
    """One MTM/IV/greeks/book sample (camelCase, epoch-ms t) for the WS + hypertable."""
    legs = _open(pos)
    spot = mv.spot(pos.underlying)
    greeks = []
    leg_rows: dict[str, dict[str, float]] = {}
    for lg in legs:
        q = mv.quote(lg.symbol)
        mark = q.mark if (q and q.mark) else lg.entry
        d, th, vg = leg_greeks(lg, q, spot)
        greeks.append(LegGreeks(lg.side, lg.qty, lg.contract_value, d, th, vg))
        sgn = leg_sign(lg.side) * lg.qty * lg.contract_value
        leg_rows[str(lg.id)] = {
            "pnl": leg_pnl(lg.side, lg.qty, lg.contract_value, lg.entry, mark),
            "iv": q.iv if q else 0.0,
            "delta": sgn * d,
            "theta": sgn * th,
            "vega": sgn * vg,
            "bid": q.bid if q else 0.0,
            "ask": q.ask if q else 0.0,
        }
    ng = net_greeks(greeks)
    atm_iv = {e: mv.atm_iv(pos.underlying, e) for e in {lg.expiry for lg in legs}}
    net = net_pnl(
        [LegQuote(lg.side, lg.qty, lg.contract_value, lg.entry, _mark(mv, lg)) for lg in legs]
    )
    return {
        "t": _ms(now),
        "pnl": net,
        "pnlOpen": net,
        "pnlHigh": net,
        "pnlLow": net,
        "delta": ng["delta"],
        "theta": ng["theta"],
        "vega": ng["vega"],
        "atmIv": atm_iv,
        "legs": leg_rows,
    }


def position_entry_slippage(pos: Position, mv: MarketView) -> float:
    return sum(
        leg_entry_slippage(lg.entry, lg.mark_at_entry, lg.qty, lg.contract_value)
        for lg in _open(pos)
    )


# --- mutations -------------------------------------------------------------- #
async def place_strategy(
    session: AsyncSession, user_id: uuid.UUID, app_state: Any, req: Any
) -> uuid.UUID:
    mv = MarketView(app_state.market)
    if not mv.fresh():
        raise ValueError("market feed is stale — refusing to fill at frozen prices")
    underlying = req.legs[0].underlying
    now = datetime.now(UTC)

    leg_models: list[Leg] = []
    for spec in req.legs:
        q = mv.quote(spec.symbol)
        if q is None or (not q.mark and not q.bid and not q.ask):
            raise ValueError(f"no live quote for {spec.symbol} — cannot fill")
        fill = entry_fill(spec.side, q.bid, q.ask, q.mark or q.bid or q.ask)
        leg_models.append(
            Leg(
                user_id=user_id,
                symbol=spec.symbol,
                product_id=spec.product_id,
                underlying=spec.underlying,
                type=spec.type,
                strike=spec.strike,
                contract_value=spec.contract_value,
                expiry=spec.expiry,
                dte=spec.dte,
                side=spec.side,
                qty=spec.qty,
                entry=fill,
                mark_at_entry=q.mark or fill,
                spot_at_entry=mv.spot(spec.underlying) or 0.0,
                target_pnl=None,
                stop_pnl=None,
                auto_exit=False,
                close_scope="leg",
                status="open",
                exit_price=None,
                exit_at=None,
                exit_reason=None,
                exit_gross=None,
                exit_fees=None,
            )
        )

    web_jwt = await get_secret(session, user_id, "delta_web_jwt")
    mq = await quote_margin(
        app_state, underlying, [(s.product_id, s.side, s.qty) for s in req.legs], web_jwt=web_jwt
    )
    margin, badge = mq.margin, mq.badge

    pos = Position(
        user_id=user_id,
        name=req.name,
        underlying=underlying,
        expiry=req.legs[0].expiry,
        margin=margin,
        entry_margin=margin,
        margin_badge=badge,
        opened_at=now,
        status="open",
        target_pnl=req.target_pnl,
        stop_loss_amount=req.stop_loss_amount,
        stop_loss_pct_of_margin=req.stop_loss_pct_of_margin,
        auto_exit=req.auto_exit,
        auto_exit_suspended=False,
        closed_at=None,
        close_reason=None,
    )
    pos.legs = leg_models
    session.add(pos)
    await session.flush()  # assign ids

    account = await _account(session, user_id)
    account.balance_usd -= margin
    session.add(
        LedgerEntry(
            user_id=user_id,
            t=now,
            type="margin_reserve",
            amount=-margin,
            balance_after=account.balance_usd,
            ref=pos.name,
        )
    )
    session.add(
        Log(
            user_id=user_id,
            t=now,
            action="PLACE",
            detail=f"{pos.name} · {len(leg_models)} legs · margin ${margin:,.2f}",
            tone="info",
        )
    )

    s = build_sample(pos, mv, now)
    session.add(
        StrategySeries(
            time=now,
            position_id=pos.id,
            user_id=user_id,
            pnl=s["pnl"],
            delta=s["delta"],
            theta=s["theta"],
            vega=s["vega"],
            atm_iv=s["atmIv"],
            legs=s["legs"],
        )
    )
    await session.flush()
    return pos.id


async def close_position(
    session: AsyncSession, user_id: uuid.UUID, app_state: Any, pos_id: uuid.UUID, reason: str | None
) -> bool:
    pos = await _get_position(session, user_id, pos_id)
    if pos is None or pos.status == "closed":
        return False
    mv = MarketView(app_state.market)
    legs = _open(pos)
    now = datetime.now(UTC)
    why = reason or "manual"

    # gross at exit fills (carries exit slippage); fees = entry + exit per leg
    exit_quotes: dict[str, Quote | None] = {lg.symbol: mv.quote(lg.symbol) for lg in legs}

    def _exit_price(lg: Leg) -> float:
        q = exit_quotes[lg.symbol]
        return exit_fill(lg.side, q.bid if q else 0, q.ask if q else 0, q.mark if q else lg.entry)

    gross = net_pnl(
        [LegQuote(lg.side, lg.qty, lg.contract_value, lg.entry, _exit_price(lg)) for lg in legs]
    )
    fees = 0.0
    for lg in legs:
        ex = _exit_price(lg)
        fees += leg_fee(lg.entry, lg.spot_at_entry, lg.contract_value, lg.qty)
        fees += leg_fee(ex, mv.spot(lg.underlying) or lg.spot_at_entry, lg.contract_value, lg.qty)

    account = await _account(session, user_id)
    after_release = account.balance_usd + pos.margin
    after_fees = after_release - fees
    balance_after = after_fees + gross
    account.balance_usd = balance_after

    for lg in legs:
        ex = _exit_price(lg)
        g = leg_pnl(lg.side, lg.qty, lg.contract_value, lg.entry, ex)
        f = leg_fee(lg.entry, lg.spot_at_entry, lg.contract_value, lg.qty) + leg_fee(
            ex, mv.spot(lg.underlying) or lg.spot_at_entry, lg.contract_value, lg.qty
        )
        lg.status, lg.exit_price, lg.exit_at, lg.exit_reason, lg.exit_gross, lg.exit_fees = (
            "closed",
            ex,
            now,
            why,
            g,
            f,
        )
    pos.status, pos.closed_at, pos.close_reason = "closed", now, why

    net = gross - fees
    session.add(
        LedgerEntry(
            user_id=user_id,
            t=now,
            type="realized",
            amount=gross,
            balance_after=balance_after,
            ref=pos.name,
        )
    )
    session.add(
        LedgerEntry(
            user_id=user_id, t=now, type="fee", amount=-fees, balance_after=after_fees, ref=pos.name
        )
    )
    session.add(
        LedgerEntry(
            user_id=user_id,
            t=now,
            type="margin_release",
            amount=pos.margin,
            balance_after=after_release,
            ref=pos.name,
        )
    )
    sign = "pos" if net >= 0 else "neg"
    session.add(
        Log(
            user_id=user_id,
            t=now,
            action=f"CLOSE ({why})",
            detail=(
                f"{pos.name} · net {'+' if net >= 0 else ''}${net:,.2f} "
                f"(gross {'+' if gross >= 0 else ''}${gross:,.2f} − fees ${fees:,.2f})"
            ),
            tone=sign,
        )
    )
    return True


async def close_leg(
    session: AsyncSession,
    user_id: uuid.UUID,
    app_state: Any,
    pos_id: uuid.UUID,
    leg_id: uuid.UUID,
    reason: str | None,
    settle_price: float | None = None,
) -> bool:
    pos = await _get_position(session, user_id, pos_id)
    if pos is None or pos.status == "closed":
        return False
    leg = next((lg for lg in pos.legs if lg.id == leg_id and lg.status == "open"), None)
    if leg is None:
        return False
    mv = MarketView(app_state.market)
    now = datetime.now(UTC)
    why = reason or "manual"
    remaining = [lg for lg in _open(pos) if lg.id != leg_id]

    if settle_price is not None:
        # expiry settlement: exit at Delta's official settlement price, FEE-FREE
        # (auto-settlement isn't a placed trade — flagged for later validation).
        ex = settle_price
        fees = 0.0
    else:
        q = mv.quote(leg.symbol)
        ex = exit_fill(leg.side, q.bid if q else 0, q.ask if q else 0, q.mark if q else leg.entry)
        spot_now = mv.spot(leg.underlying) or leg.spot_at_entry
        fees = leg_fee(leg.entry, leg.spot_at_entry, leg.contract_value, leg.qty) + leg_fee(
            ex, spot_now, leg.contract_value, leg.qty
        )
    gross = leg_pnl(leg.side, leg.qty, leg.contract_value, leg.entry, ex)

    new_margin = 0.0
    badge = pos.margin_badge
    if remaining:
        try:
            web_jwt = await get_secret(session, user_id, "delta_web_jwt")
            mq = await quote_margin(
                app_state, pos.underlying,
                [(lg.product_id, lg.side, lg.qty) for lg in remaining], web_jwt=web_jwt,
            )
            new_margin, badge = mq.margin, mq.badge
        except ValueError:
            new_margin = pos.margin  # margin fetch failed → keep reserve

    closing = not remaining
    released = pos.margin - new_margin
    account = await _account(session, user_id)
    balance_after = account.balance_usd + gross - fees + released
    account.balance_usd = balance_after

    leg.status, leg.exit_price, leg.exit_at, leg.exit_reason, leg.exit_gross, leg.exit_fees = (
        "closed",
        ex,
        now,
        why,
        gross,
        fees,
    )
    if not closing:
        pos.margin, pos.margin_badge = new_margin, badge
    else:
        pos.status, pos.closed_at, pos.close_reason = "closed", now, why

    net = gross - fees
    label = f"{int(leg.strike)}{'CE' if leg.type == 'call' else 'PE'}"
    session.add(
        LedgerEntry(
            user_id=user_id,
            t=now,
            type="realized",
            amount=gross,
            balance_after=balance_after,
            ref=f"{pos.name} · {label}",
        )
    )
    session.add(
        LedgerEntry(
            user_id=user_id,
            t=now,
            type="fee",
            amount=-fees,
            balance_after=balance_after,
            ref=f"{pos.name} · {label}",
        )
    )
    if released:
        session.add(
            LedgerEntry(
                user_id=user_id,
                t=now,
                type="margin_release",
                amount=released,
                balance_after=balance_after,
                ref=pos.name,
            )
        )
    session.add(
        Log(
            user_id=user_id,
            t=now,
            action=f"LEG EXIT ({why})",
            detail=f"{pos.name} · {label} · exit {ex} · net {'+' if net >= 0 else ''}${net:,.2f}",
            tone="pos" if net >= 0 else "neg",
        )
    )
    return True


async def set_position_risk(
    session: AsyncSession, user_id: uuid.UUID, pos_id: uuid.UUID, patch: Any
) -> bool:
    pos = await _get_position(session, user_id, pos_id)
    if pos is None:
        return False
    if patch.target_pnl is not None or "target_pnl" in patch.model_fields_set:
        pos.target_pnl = patch.target_pnl
    if "stop_loss_amount" in patch.model_fields_set:
        pos.stop_loss_amount = patch.stop_loss_amount
    if "stop_loss_pct_of_margin" in patch.model_fields_set:
        pos.stop_loss_pct_of_margin = patch.stop_loss_pct_of_margin
    if patch.auto_exit is not None:
        pos.auto_exit = patch.auto_exit
    if "stale_hard_stop" in patch.model_fields_set and patch.stale_hard_stop is not None:
        pos.stale_hard_stop = patch.stale_hard_stop
    return True


async def set_leg_risk(
    session: AsyncSession, user_id: uuid.UUID, leg_id: uuid.UUID, patch: Any
) -> bool:
    res = await session.execute(select(Leg).where(Leg.id == leg_id, Leg.user_id == user_id))
    leg = res.scalar_one_or_none()
    if leg is None:
        return False
    if "target_pnl" in patch.model_fields_set:
        leg.target_pnl = patch.target_pnl
    if "stop_pnl" in patch.model_fields_set:
        leg.stop_pnl = patch.stop_pnl
    if patch.auto_exit is not None:
        leg.auto_exit = patch.auto_exit
    if patch.close_scope is not None:
        leg.close_scope = patch.close_scope
    return True


async def add_note(
    session: AsyncSession, user_id: uuid.UUID, pos_id: uuid.UUID, kind: str, body: str
) -> bool:
    pos = await _get_position(session, user_id, pos_id)
    if pos is None:
        return False
    session.add(
        Note(user_id=user_id, position_id=pos_id, kind=kind, body=body, at=datetime.now(UTC))
    )
    return True


# --- exit evaluation (for the tick loop) ------------------------------------ #
def exit_legs_of(pos: Position, mv: MarketView) -> list[ExitLeg]:
    return [
        ExitLeg(
            id=str(lg.id),
            pnl=leg_pnl(lg.side, lg.qty, lg.contract_value, lg.entry, _mark(mv, lg)),
            target_pnl=lg.target_pnl,
            stop_pnl=lg.stop_pnl,
            auto_exit=lg.auto_exit,
            close_scope=lg.close_scope,
            status=lg.status,
        )
        for lg in pos.legs
    ]


# --- expiry settlement (1D) ------------------------------------------------- #
def leg_is_expired(lg: Leg, now: datetime) -> bool:
    """True once the leg's contract has passed its 12:00 UTC settlement instant."""
    try:
        e = date.fromisoformat(lg.expiry)
    except ValueError:
        return False
    return not is_expiry_live(e, now)


async def fetch_settlement_prices(client: Any) -> dict[str, float]:
    """symbol -> Delta official settlement price for recently-expired BTC/ETH options
    (read-only /v2/products). First page (page_size 1000) covers ~the last week of
    daily expiries, which is all a promptly-settled leg needs."""
    data = await client.get(
        "/v2/products",
        params={
            "states": "expired",
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": ",".join(UNDERLYINGS),
            "page_size": 1000,
        },
    )
    out: dict[str, float] = {}
    for p in data.get("result", []) if isinstance(data, dict) else []:
        sym, sp = p.get("symbol"), p.get("settlement_price")
        if sym and sp is not None:
            out[sym] = float(sp)
    return out


async def settle_expired_legs(session: AsyncSession, app_state: Any) -> int:
    """Settle every open leg past its expiry at Delta's official settlement price
    (FEE-FREE; see close_leg). Legs whose price Delta hasn't published yet are skipped
    and retried on the next call. Returns the number of legs settled."""
    now = datetime.now(UTC)
    res = await session.execute(
        select(Position).where(Position.status == "open").options(selectinload(Position.legs))
    )
    pending = [
        (p, lg)
        for p in res.scalars().all()
        for lg in p.legs
        if lg.status == "open" and leg_is_expired(lg, now)
    ]
    if not pending:
        return 0
    prices = await fetch_settlement_prices(app_state.delta)
    settled = 0
    for p, lg in pending:
        sp = prices.get(lg.symbol)
        if sp is None:
            continue  # not published yet → retry next cycle
        ok = await close_leg(
            session, p.user_id, app_state, p.id, lg.id, "settlement", settle_price=sp
        )
        if ok:
            settled += 1
    return settled


# Feed must be stale at least this long before a stale hard-stop acts — ignores a
# momentary glitch so we never exit on a 1-tick blip (see exit_engine).
STALE_HARD_STOP_GRACE_S = 30.0


def evaluate_position_exit(pos: Position, mv: MarketView) -> Any:
    age = mv.m.feed_status().get("age_seconds")
    grace_elapsed = isinstance(age, (int, float)) and age > STALE_HARD_STOP_GRACE_S
    return evaluate_exit(
        exit_legs_of(pos, mv),
        net_pnl=position_pnl(pos, mv),
        margin=pos.margin,
        combined_stop=CombinedStop(pos.stop_loss_amount, pos.stop_loss_pct_of_margin),
        combined_auto_exit=pos.auto_exit,
        stale=not mv.fresh(),
        stale_hard_stop=pos.stale_hard_stop,
        stale_grace_elapsed=grace_elapsed,
    )


# --- serialization (camelCase, epoch-ms — matches frontend types) ----------- #
def leg_dict(lg: Leg, mv: MarketView) -> dict[str, Any]:
    q = mv.quote(lg.symbol)
    closed = lg.status == "closed"
    live_pnl = (
        (lg.exit_gross or 0) - (lg.exit_fees or 0)
        if closed
        else leg_pnl(lg.side, lg.qty, lg.contract_value, lg.entry, _mark(mv, lg))
    )
    return {
        "id": str(lg.id),
        "symbol": lg.symbol,
        "productId": lg.product_id,
        "underlying": lg.underlying,
        "type": lg.type,
        "strike": lg.strike,
        "contractValue": lg.contract_value,
        "expiry": lg.expiry,
        "dte": lg.dte,
        "side": lg.side,
        "qty": lg.qty,
        "entry": lg.entry,
        "markAtEntry": lg.mark_at_entry,
        "spotAtEntry": lg.spot_at_entry,
        "targetPnl": lg.target_pnl,
        "stopPnl": lg.stop_pnl,
        "autoExit": lg.auto_exit,
        "closeScope": lg.close_scope,
        "status": lg.status,
        "exitPrice": lg.exit_price,
        "exitAt": _ms(lg.exit_at),
        "exitReason": lg.exit_reason,
        "exitGross": lg.exit_gross,
        "exitFees": lg.exit_fees,
        # live book (best-bid/ask/spread) + mark/iv/pnl
        "mark": q.mark if q else lg.entry,
        "iv": q.iv if q else 0.0,
        "bid": q.bid if q else 0.0,
        "ask": q.ask if q else 0.0,
        "spread": spread(q.bid, q.ask) if q else 0.0,
        "pnl": live_pnl,
    }


def series_dict(s: StrategySeries) -> dict[str, Any]:
    return {
        "t": _ms(s.time),
        "pnl": s.pnl,
        "pnlOpen": s.pnl_open if s.pnl_open is not None else s.pnl,
        "pnlHigh": s.pnl_high if s.pnl_high is not None else s.pnl,
        "pnlLow": s.pnl_low if s.pnl_low is not None else s.pnl,
        "delta": s.delta,
        "theta": s.theta,
        "vega": s.vega,
        "atmIv": s.atm_iv,
        "legs": s.legs,
    }


async def _rollup_series(
    session: AsyncSession,
    pos_id: uuid.UUID,
    bucket_seconds: int,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """One uniform OHLC rollup of net MTM (per-leg + Greeks = last-in-bucket) for [start, end)."""
    rows = await session.execute(
        text(
            f"SELECT time_bucket(make_interval(secs => {int(bucket_seconds)}), time) AS bucket, "
            "first(pnl_open, time) AS o, max(pnl_high) AS h, min(pnl_low) AS l, "
            "last(pnl, time) AS c, "
            "last(delta, time) AS delta, last(theta, time) AS theta, last(vega, time) AS vega, "
            "last(atm_iv, time) AS atm_iv, last(legs, time) AS legs "
            "FROM strategy_series WHERE position_id = :pid AND time >= :start AND time < :end "
            "GROUP BY bucket ORDER BY bucket"
        ),
        {"pid": pos_id, "start": start, "end": end},
    )
    return [
        {
            "t": int(r.bucket.timestamp() * 1000),
            "pnl": r.c, "pnlOpen": r.o, "pnlHigh": r.h, "pnlLow": r.l,
            "delta": r.delta, "theta": r.theta, "vega": r.vega,
            "atmIv": r.atm_iv or {}, "legs": r.legs or {},
        }
        for r in rows
    ]


def _assemble_series(
    body: list[dict[str, Any]], tail: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Body up to where the 1s tail begins, then the tail (mirrors the old bounded_series seam)."""
    if not tail:
        return body
    first_t = tail[0]["t"]
    return [p for p in body if p["t"] < first_t] + list(tail)


async def build_position_series(
    session: AsyncSession,
    pos: Position,
    ring_full: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Uniform-by-age history body + always-1s live tail (open) for one position."""
    tail = [
        {k: s[k] for k in ("t", "pnl", "pnlOpen", "pnlHigh", "pnlLow",
                           "delta", "theta", "vega", "atmIv", "legs")}
        for s in (list(ring_full) if ring_full else [])
    ]
    # span = lifetime for closed, age for open
    end = pos.closed_at if (pos.status != "open" and pos.closed_at) else datetime.now(UTC)
    span = (end - pos.opened_at).total_seconds()
    res = body_resolution_seconds(span)
    # body covers entry → where the tail starts (or → end when there's no tail)
    body_end = datetime.fromtimestamp(tail[0]["t"] / 1000, UTC) if tail else end
    if res == 1:
        body: list[dict[str, Any]] = []  # whole thing is in the ring / ≤15 min
    elif res == 10:
        rows = await session.execute(
            select(StrategySeries)
            .where(
                StrategySeries.position_id == pos.id,
                StrategySeries.time < body_end,
            )
            .order_by(StrategySeries.time)
        )
        body = [series_dict(r) for r in rows.scalars().all()]
    else:
        body = await _rollup_series(session, pos.id, res, pos.opened_at, body_end)
    return _assemble_series(body, tail)


def _net_greeks(pos: Position, mv: MarketView) -> dict[str, float]:
    rows = []
    for lg in _open(pos):
        q = mv.quote(lg.symbol)
        rows.append(
            LegGreeks(
                lg.side,
                lg.qty,
                lg.contract_value,
                q.delta if q else 0,
                q.theta if q else 0,
                q.vega if q else 0,
            )
        )
    return net_greeks(rows)


def position_dict(pos: Position, mv: MarketView, series: list[dict[str, Any]]) -> dict[str, Any]:
    """Full position incl. its (already-serialized) series — for GET /api/state."""
    g = _net_greeks(pos, mv)
    return {
        "id": str(pos.id),
        "name": pos.name,
        "underlying": pos.underlying,
        "expiry": pos.expiry,
        "legs": [leg_dict(lg, mv) for lg in pos.legs],
        "margin": pos.margin,
        "entryMargin": pos.entry_margin,
        "marginBadge": pos.margin_badge,
        "openedAt": _ms(pos.opened_at),
        "status": pos.status,
        "targetPnl": pos.target_pnl,
        "stopLossAmount": pos.stop_loss_amount,
        "stopLossPctOfMargin": pos.stop_loss_pct_of_margin,
        "autoExit": pos.auto_exit,
        "autoExitSuspended": pos.auto_exit_suspended,
        "staleHardStop": pos.stale_hard_stop,
        "closedAt": _ms(pos.closed_at),
        "closeReason": pos.close_reason,
        "series": series,
        "notes": [{"kind": n.kind, "body": n.body, "at": _ms(n.at)} for n in pos.notes],
        "pnl": position_pnl(pos, mv),
        "delta": g["delta"],
        "theta": g["theta"],
        "vega": g["vega"],
        "entrySlippage": position_entry_slippage(pos, mv),
    }


def position_live_dict(pos: Position, mv: MarketView, now: datetime) -> dict[str, Any]:
    """Lightweight live update (no series) + one fresh sample to append — for the WS tick."""
    g = _net_greeks(pos, mv)
    return {
        "id": str(pos.id),
        "status": pos.status,
        "margin": pos.margin,
        "marginBadge": pos.margin_badge,
        "autoExitSuspended": pos.auto_exit_suspended,
        "closedAt": _ms(pos.closed_at),
        "closeReason": pos.close_reason,
        "legs": [leg_dict(lg, mv) for lg in pos.legs],
        "pnl": position_pnl(pos, mv),
        "delta": g["delta"],
        "theta": g["theta"],
        "vega": g["vega"],
        "entrySlippage": position_entry_slippage(pos, mv),
        "sample": build_sample(pos, mv, now),
    }


# Keep raw 10s rows for the recent window; older history is served as 1-minute NET
# rollups (no per-leg/IV detail) so GET /api/state doesn't materialize tens of thousands
# of rows for a long-open position (the rehydrate memory spike). Net-only old by design.
DB_RAW_WINDOW_S = 3 * 24 * 60 * 60  # 3 days

TAIL_SECONDS = 15 * 60  # last 15 min always served at 1s from the ring (matches RING_MAXLEN)


def body_resolution_seconds(span_seconds: float) -> int:
    """Uniform history-body resolution chosen by the position's span.

    Span is the position's age (open) or its lifetime (closed).
    """
    if span_seconds <= 15 * 60:
        return 1
    if span_seconds <= 12 * 3600:
        return 10
    if span_seconds <= 24 * 3600:
        return 60
    return 300


async def get_state(
    session: AsyncSession,
    user_id: uuid.UUID,
    mv: MarketView,
    series_store: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    account = await _account(session, user_id)
    res = await session.execute(
        select(Position)
        .where(Position.user_id == user_id)
        .options(selectinload(Position.legs), selectinload(Position.notes))
        .order_by(Position.opened_at.desc())
    )
    positions = list(res.scalars().all())
    # Light table snapshot: no history. Each position's series is loaded lazily
    # via GET /api/positions/{id}/series (see the per-position series builder).
    pos_dicts = [position_dict(p, mv, []) for p in positions]
    led = (
        (
            await session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.user_id == user_id)
                .order_by(LedgerEntry.t.desc())
            )
        )
        .scalars()
        .all()
    )
    logs = (
        (await session.execute(select(Log).where(Log.user_id == user_id).order_by(Log.t.desc())))
        .scalars()
        .all()
    )
    return {
        "account": {
            "balance": account.balance_usd,
            "currency": account.currency,
            "startBalance": account.start_balance_usd,
        },
        "positions": pos_dicts,
        "ledger": [
            {
                "t": _ms(e.t),
                "type": e.type,
                "amount": e.amount,
                "balanceAfter": e.balance_after,
                "ref": e.ref,
            }
            for e in led
        ],
        "logs": [
            {"t": _ms(lg.t), "action": lg.action, "detail": lg.detail, "tone": lg.tone}
            for lg in logs
        ],
    }
