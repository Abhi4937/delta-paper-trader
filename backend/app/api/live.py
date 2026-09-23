"""Live (real Delta) endpoints — every route requires a 2FA-cleared session.

Arming is the only way to switch on auto-exit for a live group, so the SL-vs-liquidation
check always runs first: a group whose liquidation would come before its SL cannot be armed.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import require_mfa
from app.auth.vault import get_secret
from app.config import get_settings
from app.db.models import Log, Position
from app.db.session import get_session
from app.engines.money import entry_fill
from app.live import journal, risk
from app.live.alerts import explain_telegram, send_telegram
from app.live.client import DeltaError
from app.live.sync import LiveSync, _mark, basket_floor, risk_legs, usd_balance, usd_wallet
from app.sim.marketview import MarketView

router = APIRouter(prefix="/api/live", tags=["live"])


def _sync(request: Request) -> LiveSync:
    return cast(LiveSync, request.app.state.live)


class ArmIn(BaseModel):
    armed: bool


@router.get("/status")
async def status(request: Request, user_id: uuid.UUID = Depends(require_mfa)) -> dict[str, Any]:
    sync = _sync(request)
    return {
        "tradingEnabled": get_settings().live_trading_enabled,
        "alerts": sync.alerts.active(user_id),
        "checks": sync.liq.get(user_id, {}),
        "usage": sync.usage.get(user_id),
    }


@router.post("/groups/{pos_id}/arm")
async def arm(
    pos_id: uuid.UUID,
    body: ArmIn,
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    user_id: uuid.UUID = Depends(require_mfa),
) -> dict[str, Any]:
    sync = _sync(request)
    res = await session.execute(
        select(Position)
        .where(Position.user_id == user_id, Position.source == "live", Position.status == "open")
        .options(selectinload(Position.legs))
    )
    groups = list(res.scalars().all())
    g = next((p for p in groups if p.id == pos_id), None)
    if g is None:
        raise HTTPException(404, "live group not found")
    if g.exiting:
        raise HTTPException(409, "group is already exiting")
    client = await sync.client_for(session, user_id)
    if client is None:
        raise HTTPException(400, "no Delta trade key saved")

    check: dict[str, Any] | None = None
    if body.armed:
        if basket_floor(g) is None and not any(
            lg.stop_pnl is not None for lg in g.legs if lg.status == "open"
        ):
            raise HTTPException(400, "set a basket SL or a leg SL before arming")
        mv = MarketView(request.app.state.market)
        try:
            balance = usd_balance(await client.wallet())
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"couldn't read the Delta wallet: {type(e).__name__}") from e
        if balance is None:
            raise HTTPException(502, "no USD balance in the Delta wallet response")
        chk = risk.check_sl_vs_liquidation(
            risk_legs(g, groups, lambda lg: _mark(mv, lg)),
            spot=mv.spot(g.underlying),
            balance=balance,
            basket_floor=basket_floor(g),
        )
        check = asdict(chk)
        sync.liq.setdefault(user_id, {})[str(g.id)] = check
        if chk.verdict == "red":
            raise HTTPException(409, f"not armed: {chk.reason}")

    g.auto_exit = body.armed
    await sync.sync_stops(session, user_id, client, groups)  # place / cancel native stops now
    await session.commit()
    return {"armed": g.auto_exit, "check": check}


@router.post("/groups/{pos_id}/exit")
async def exit_now(
    pos_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    user_id: uuid.UUID = Depends(require_mfa),
) -> dict[str, bool]:
    ok = await _sync(request).manual_exit(session, user_id, pos_id)
    if not ok:
        raise HTTPException(409, "not found, not open, or an exit is already running")
    return {"started": True}


@router.post("/telegram/test")
async def telegram_test(
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    user_id: uuid.UUID = Depends(require_mfa),
) -> dict[str, bool]:
    token = await get_secret(session, user_id, "telegram_bot_token")
    chat = await get_secret(session, user_id, "telegram_chat_id")
    if not token or not chat:
        raise HTTPException(400, "save a Telegram bot token and chat id first")
    ok, why = await send_telegram(
        request.app.state.http,
        token,
        chat,
        "Paper Trader: test alert — live alerts will arrive here.",
    )
    if not ok:
        raise HTTPException(502, f"Telegram: {explain_telegram(why)}")
    return {"sent": True}


@router.get("/journal")
async def live_journal(
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    user_id: uuid.UUID = Depends(require_mfa),
) -> dict[str, Any]:
    """Every live trade (snapshot frozen at close; running stats while open) + the live log.
    Separate from the paper Logs page."""
    rings = getattr(request.app.state, "sim_series", {})
    res = await session.execute(
        select(Position)
        .where(Position.user_id == user_id, Position.source == "live")
        .options(selectinload(Position.legs))
        .order_by(Position.opened_at.desc())
    )
    trades = [await journal.trade_summary(session, p, rings.get(str(p.id))) for p in res.scalars()]
    logs = await session.execute(
        select(Log)
        .where(Log.user_id == user_id, Log.action.like("LIVE%"))
        .order_by(Log.t.desc())
        .limit(5000)
    )
    return {
        "trades": trades,
        "logs": [
            {
                "t": int(lg.t.timestamp() * 1000),
                "action": lg.action,
                "detail": lg.detail,
                "tone": lg.tone,
            }
            for lg in logs.scalars()
        ],
    }


@router.post("/test-key")
async def test_key(
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    user_id: uuid.UUID = Depends(require_mfa),
) -> dict[str, Any]:
    """Read-only check of the saved Delta key: wallet + positions. No order is sent, so this
    cannot prove Trading permission — only Delta accepting a real order can."""
    client = await _sync(request).client_for(session, user_id)
    if client is None:
        raise HTTPException(400, "save a Delta trade key and secret first")
    try:
        wallet = usd_wallet(await client.wallet())
        positions = await client.positions()
    except DeltaError as e:
        raise HTTPException(400, f"Delta refused the key: {e.explain()}") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"couldn't reach Delta ({type(e).__name__})") from e
    return {
        "ok": True,
        "balance": wallet["balance"] if wallet else None,
        "available": wallet["available"] if wallet else None,
        "openPositions": sum(1 for p in positions if int(p.get("size") or 0) != 0),
    }


@router.get("/account")
async def account(
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    user_id: uuid.UUID = Depends(require_mfa),
) -> dict[str, Any]:
    """Real Delta USD wallet: total balance and what's free for new margin."""
    client = await _sync(request).client_for(session, user_id)
    if client is None:
        raise HTTPException(400, "no Delta trade key saved")
    try:
        wallet = usd_wallet(await client.wallet())
    except DeltaError as e:
        raise HTTPException(400, f"Delta refused the key: {e.explain()}") from e
    if wallet is None:
        raise HTTPException(502, "no USD balance in the Delta wallet response")
    return wallet


class PrecheckLeg(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: float


class PrecheckIn(BaseModel):
    legs: list[PrecheckLeg]
    basket_sl: float | None = None  # planned basket loss limit, USD (magnitude)


@router.post("/precheck")
async def precheck(
    body: PrecheckIn,
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    user_id: uuid.UUID = Depends(require_mfa),
) -> dict[str, Any]:
    """Before placing a trade on Delta: with the planned basket SL, would the SL fire before
    liquidation, given the real wallet and the live positions already open?"""
    sync = _sync(request)
    client = await sync.client_for(session, user_id)
    if client is None:
        raise HTTPException(400, "no Delta trade key saved")
    try:
        wallet = usd_wallet(await client.wallet())
    except DeltaError as e:
        raise HTTPException(400, f"Delta refused the key: {e.explain()}") from e
    if wallet is None or not body.legs:
        raise HTTPException(400, "need legs and a readable Delta wallet")
    tickers = request.app.state.market.tickers
    mv = MarketView(request.app.state.market)
    new: list[risk.RiskLeg] = []
    underlying = ""
    for lg in body.legs:
        t = tickers.get(lg.symbol)
        q = mv.quote(lg.symbol)
        if t is None or q is None:
            raise HTTPException(400, f"no live quote for {lg.symbol}")
        mark = q.mark if q.mark is not None else 0.0
        underlying = str(t.get("underlying_asset_symbol") or "")
        c = journal_contract(t)
        new.append(
            risk.RiskLeg(
                type=c["type"],
                side=lg.side,
                qty=lg.qty,
                strike=c["strike"],
                dte_days=c["dte_days"],
                cv=c["cv"],
                entry=entry_fill(lg.side, q.bid, q.ask, mark),
                mark=mark,
            )
        )
    res = await session.execute(
        select(Position)
        .where(Position.user_id == user_id, Position.source == "live", Position.status == "open")
        .options(selectinload(Position.legs))
    )
    open_groups = [g for g in res.scalars() if g.underlying == underlying]
    others = [
        risk.RiskLeg(
            type=lg.type,
            side=lg.side,
            qty=lg.qty,
            strike=lg.strike,
            dte_days=max(lg.dte, 1 / 24),
            cv=lg.contract_value,
            entry=lg.entry,
            mark=_mark(mv, lg),
            in_group=False,
        )
        for g in open_groups
        for lg in g.legs
        if lg.status == "open"
    ]
    chk = risk.check_sl_vs_liquidation(
        new + others,
        spot=mv.spot(underlying),
        balance=wallet["balance"],
        basket_floor=body.basket_sl,
    )
    return {"check": asdict(chk), **wallet}


def journal_contract(t: dict[str, Any]) -> dict[str, Any]:
    """Option fields a risk leg needs, from a /v2/tickers row."""
    from datetime import UTC, datetime

    from app.services.chain import normalize_contract

    c = normalize_contract(t)
    if c is None or c.expiry is None:
        raise HTTPException(400, f"{t.get('symbol')} is not a tracked option")
    dte = (
        datetime.combine(c.expiry, datetime.min.time(), UTC).replace(hour=12) - datetime.now(UTC)
    ).total_seconds() / 86400
    return {
        "type": c.option_type,
        "strike": c.strike,
        "cv": c.contract_value or 0.001,
        "dte_days": max(dte, 1 / 24),
    }
