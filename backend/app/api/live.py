"""Live (real Delta) endpoints — every route requires a 2FA-cleared session.

Arming is the only way to switch on auto-exit for a live group, so the SL-vs-liquidation
check always runs first: a group whose liquidation would come before its SL cannot be armed.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import require_mfa
from app.auth.vault import get_secret
from app.config import get_settings
from app.db.models import Position
from app.db.session import get_session
from app.live import risk
from app.live.alerts import send_telegram
from app.live.sync import LiveSync, _mark, basket_floor, risk_legs, usd_balance
from app.sim.marketview import MarketView

router = APIRouter(prefix="/api/live", tags=["live"])


def _sync(request: Request) -> LiveSync:
    return request.app.state.live


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
    ok = await send_telegram(
        request.app.state.http,
        token,
        chat,
        "Paper Trader: test alert — live alerts will arrive here.",
    )
    if not ok:
        raise HTTPException(502, "Telegram rejected the message — check the token and chat id")
    return {"sent": True}
