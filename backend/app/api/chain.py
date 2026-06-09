"""Option-chain REST endpoints (read-only market data)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.delta.rest import DeltaRestClient
from app.services import chain as chain_svc

router = APIRouter(prefix="/api/chain", tags=["chain"])


def get_delta(request: Request) -> DeltaRestClient:
    return request.app.state.delta  # type: ignore[no-any-return]


DeltaDep = Annotated[DeltaRestClient, Depends(get_delta)]


@router.get("/expiries")
async def expiries(
    delta: DeltaDep,
    underlying: Annotated[str, Query()] = "BTC",
) -> dict:
    u = underlying.upper()
    tickers = await chain_svc.fetch_option_tickers(delta, u)
    return {"underlying": u, "expiries": chain_svc.list_expiries(tickers)}


@router.get("")
async def chain(
    delta: DeltaDep,
    underlying: Annotated[str, Query()] = "BTC",
    expiry: Annotated[date | None, Query(description="ISO date; defaults to nearest")] = None,
) -> chain_svc.OptionChain:
    u = underlying.upper()
    tickers = await chain_svc.fetch_option_tickers(delta, u)
    exp = expiry or chain_svc.default_expiry(chain_svc.list_expiries(tickers))
    return chain_svc.build_chain(tickers, u, exp)
