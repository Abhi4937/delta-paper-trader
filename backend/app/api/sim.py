"""Sim state API: full state (GET) + mutations (place/close/leg-close/risk/note)
+ a live WS tick. The authenticated user is resolved via `current_user` (Supabase
token, or the dev stub when running locally without a token); every query is user-scoped.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import vault
from app.auth.deps import current_user, resolve_ws_user
from app.db.models import Account, Position, User
from app.db.session import SessionLocal, get_session
from app.sim import service
from app.sim.marketview import MarketView
from app.sim.schemas import (
    CloseRequest,
    DraftIn,
    LegRiskPatch,
    NoteIn,
    PlaceRequest,
    PositionRiskPatch,
)

router = APIRouter(prefix="/api", tags=["sim"])


def _mv(request: Request) -> MarketView:
    return MarketView(request.app.state.market)


def _store(request: Request) -> dict[str, Any]:
    return getattr(request.app.state, "sim_series", {})


async def _state(request: Request, session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    return await service.get_state(session, user_id, _mv(request), _store(request))


@router.get("/me")
async def me(
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    """The authenticated user's identity + admin flag + whether they hold live trade keys
    (drives the UI nav/header and the 'force 2FA when live keys exist' rule)."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    status = await vault.secret_status(session, user_id)
    has_live_keys = bool(status.get("delta_trade_key") and status.get("delta_trade_secret"))
    return {
        "email": user.email,
        "displayName": user.display_name,
        "isAdmin": user.is_admin,
        "hasLiveKeys": has_live_keys,
    }


async def _account(session: AsyncSession, user_id: uuid.UUID) -> Account:
    return (
        await session.execute(select(Account).where(Account.user_id == user_id))
    ).scalar_one()


@router.get("/draft")
async def get_draft(
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    """The user's saved builder draft basket (syncs across their devices)."""
    acct = await _account(session, user_id)
    return {"legs": acct.draft_basket or []}


@router.put("/draft")
async def put_draft(
    body: DraftIn,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    acct = await _account(session, user_id)
    acct.draft_basket = body.legs
    await session.flush()
    return {"ok": True}


@router.get("/state")
async def get_state(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    return await _state(request, session, user_id)


@router.post("/strategies")
async def place(
    req: PlaceRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    if not req.legs:
        raise HTTPException(400, "no legs")
    try:
        await service.place_strategy(session, user_id, request.app.state, req)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await _state(request, session, user_id)


@router.post("/strategies/{pos_id}/close")
async def close(
    pos_id: uuid.UUID,
    req: CloseRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    ok = await service.close_position(session, user_id, request.app.state, pos_id, req.reason)
    if not ok:
        raise HTTPException(404, "position not found or already closed")
    return await _state(request, session, user_id)


@router.post("/strategies/{pos_id}/legs/{leg_id}/close")
async def close_leg(
    pos_id: uuid.UUID,
    leg_id: uuid.UUID,
    req: CloseRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    ok = await service.close_leg(session, user_id, request.app.state, pos_id, leg_id, req.reason)
    if not ok:
        raise HTTPException(404, "leg not found or already closed")
    return await _state(request, session, user_id)


@router.patch("/strategies/{pos_id}/risk")
async def position_risk(
    pos_id: uuid.UUID,
    patch: PositionRiskPatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    ok = await service.set_position_risk(session, user_id, pos_id, patch)
    if not ok:
        raise HTTPException(404, "position not found")
    return await _state(request, session, user_id)


@router.patch("/legs/{leg_id}/risk")
async def leg_risk(
    leg_id: uuid.UUID,
    patch: LegRiskPatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    ok = await service.set_leg_risk(session, user_id, leg_id, patch)
    if not ok:
        raise HTTPException(404, "leg not found")
    return await _state(request, session, user_id)


@router.post("/strategies/{pos_id}/notes")
async def add_note(
    pos_id: uuid.UUID,
    note: NoteIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    ok = await service.add_note(session, user_id, pos_id, note.kind, note.body)
    if not ok:
        raise HTTPException(404, "position not found")
    return await _state(request, session, user_id)


@router.websocket("/ws/state")
async def ws_state(websocket: WebSocket) -> None:
    """Live tick (~1s): balance + per-open-position live values + a sample to append.
    `openIds` lets the client detect server-side auto-exits and refetch GET /api/state."""
    # Token rides in the Sec-WebSocket-Protocol header as ["jwt", "<token>"] — not the
    # URL — so it never lands in access logs. Echo the "jwt" subprotocol on accept.
    raw_proto = websocket.headers.get("sec-websocket-protocol", "")
    protos = [p.strip() for p in raw_proto.split(",") if p.strip()]
    token = protos[1] if len(protos) >= 2 and protos[0] == "jwt" else None
    await websocket.accept(subprotocol="jwt" if token else None)
    async with SessionLocal() as session:
        try:
            user_id = await resolve_ws_user(token, session)
        except HTTPException:
            await websocket.close(code=4401)  # auth failed
            return
    mv = MarketView(websocket.app.state.market)
    store = getattr(websocket.app.state, "sim_series", {})
    try:
        while True:
            async with SessionLocal() as session:
                account = (
                    await session.execute(select(Account).where(Account.user_id == user_id))
                ).scalar_one()
                res = await session.execute(
                    select(Position)
                    .where(Position.user_id == user_id, Position.status == "open")
                    .options(selectinload(Position.legs))
                )
                positions = list(res.scalars().all())
                now = datetime.now(UTC)
                payload = {
                    "type": "tick",
                    "balance": account.balance_usd,
                    "currency": account.currency,
                    "openIds": [str(p.id) for p in positions],
                    "positions": [service.position_live_dict(p, mv, now) for p in positions],
                }
            # keep series rings from growing unbounded for closed positions
            _ = store
            await websocket.send_json(payload)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await websocket.close()
        raise
