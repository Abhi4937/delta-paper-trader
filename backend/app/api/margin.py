"""Margin REST endpoint: exact live margin with local fallback."""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.auth.vault import get_secret
from app.db.session import get_session
from app.delta.rest import DeltaRestClient
from app.services import chain as chain_svc
from app.services.margin import BasketLeg, MarginQuote, MarginService

router = APIRouter(prefix="/api/margin", tags=["margin"])


class LegIn(BaseModel):
    product_id: int
    side: str  # "buy" | "sell"
    size: int = 1


class MarginRequest(BaseModel):
    underlying: str = "BTC"
    legs: list[LegIn]


@router.post("")
async def margin(
    req: MarginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> MarginQuote:
    delta: DeltaRestClient = request.app.state.delta
    http: httpx.AsyncClient = request.app.state.http
    service: MarginService = request.app.state.margin

    if not req.legs:
        raise HTTPException(400, "no legs")

    # Resolve every leg to a live Contract by product_id (across all expiries,
    # so calendars/diagonals work).
    tickers = await chain_svc.fetch_option_tickers(delta, req.underlying.upper())
    by_id = {}
    for t in tickers:
        c = chain_svc.normalize_contract(t)
        if c and c.product_id is not None:
            by_id[c.product_id] = c

    basket: list[BasketLeg] = []
    spot = 0.0
    for leg in req.legs:
        c = by_id.get(leg.product_id)
        if c is None:
            raise HTTPException(404, f"unknown product_id {leg.product_id}")
        spot = spot or (c.spot_price or 0.0)
        basket.append(BasketLeg(contract=c, side=leg.side, size=leg.size))

    # the caller's own Delta web-session token (vault) is the fallback after the global one
    web_jwt = await get_secret(session, user_id, "delta_web_jwt")
    return await service.get_margin(http, req.underlying.upper(), basket, spot, web_jwt=web_jwt)
