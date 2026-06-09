"""Request models for the sim API. State output is built as plain dicts (camelCase,
epoch-ms times) to match the frontend types directly."""

from __future__ import annotations

from pydantic import BaseModel


class PlaceLegIn(BaseModel):
    symbol: str
    product_id: int
    underlying: str
    type: str  # call | put
    strike: float
    contract_value: float
    expiry: str  # ISO date
    dte: float
    side: str  # buy | sell
    qty: float


class PlaceRequest(BaseModel):
    name: str
    legs: list[PlaceLegIn]
    target_pnl: float | None = None
    stop_loss_amount: float | None = None
    stop_loss_pct_of_margin: float | None = None
    auto_exit: bool = False


class CloseRequest(BaseModel):
    reason: str | None = None


class PositionRiskPatch(BaseModel):
    target_pnl: float | None = None
    stop_loss_amount: float | None = None
    stop_loss_pct_of_margin: float | None = None
    auto_exit: bool | None = None


class LegRiskPatch(BaseModel):
    target_pnl: float | None = None
    stop_pnl: float | None = None
    auto_exit: bool | None = None
    close_scope: str | None = None  # leg | strategy


class NoteIn(BaseModel):
    kind: str = "entry"  # entry | exit
    body: str
