"""Sim state API: full state (GET) + mutations (place/close/leg-close/risk/note)
+ a live WS tick. Stub user resolved on every call; every query is user-scoped.
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

from app.db.models import Account, Position
from app.db.session import SessionLocal, get_session
from app.sim import service
from app.sim.marketview import MarketView
from app.sim.schemas import (
    CloseRequest,
    LegRiskPatch,
    NoteIn,
    PlaceRequest,
    PositionRiskPatch,
)
from app.sim.user import ensure_stub_user

router = APIRouter(prefix="/api", tags=["sim"])


def _mv(request: Request) -> MarketView:
    return MarketView(request.app.state.market)


def _store(request: Request) -> dict[str, Any]:
    return getattr(request.app.state, "sim_series", {})


async def _state(request: Request, session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    return await service.get_state(session, user_id, _mv(request), _store(request))


@router.get("/state")
async def get_state(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    user_id = await ensure_stub_user(session)
    return await _state(request, session, user_id)


@router.post("/strategies")
async def place(
    req: PlaceRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    if not req.legs:
        raise HTTPException(400, "no legs")
    user_id = await ensure_stub_user(session)
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
) -> dict[str, Any]:
    user_id = await ensure_stub_user(session)
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
) -> dict[str, Any]:
    user_id = await ensure_stub_user(session)
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
) -> dict[str, Any]:
    user_id = await ensure_stub_user(session)
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
) -> dict[str, Any]:
    user_id = await ensure_stub_user(session)
    ok = await service.set_leg_risk(session, user_id, leg_id, patch)
    if not ok:
        raise HTTPException(404, "leg not found")
    return await _state(request, session, user_id)


@router.post("/strategies/{pos_id}/notes")
async def add_note(
    pos_id: uuid.UUID, note: NoteIn, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    user_id = await ensure_stub_user(session)
    ok = await service.add_note(session, user_id, pos_id, note.kind, note.body)
    if not ok:
        raise HTTPException(404, "position not found")
    return await _state(request, session, user_id)


@router.websocket("/ws/state")
async def ws_state(websocket: WebSocket) -> None:
    """Live tick (~1s): balance + per-open-position live values + a sample to append.
    `openIds` lets the client detect server-side auto-exits and refetch GET /api/state."""
    await websocket.accept()
    mv = MarketView(websocket.app.state.market)
    store = getattr(websocket.app.state, "sim_series", {})
    try:
        while True:
            async with SessionLocal() as session:
                user_id = await ensure_stub_user(session)
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
