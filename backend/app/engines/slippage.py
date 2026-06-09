"""Slippage / paper-fill engine (ADR 0003).

Models a paper fill the way Delta executes: walk the live L2 book (asks for buy,
bids for sell) for a size-weighted price, **capped by the allowed trading band**
with **IOC partial fills** (any size beyond in-band depth is cancelled), an
optional linear-impact overlay, and a thin/empty-book fallback. Captures the full
microstructure per fill so a data-driven model can later replace the hand-tuned
impact `k`.

All money is `Decimal` — floats never touch a fill price (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class Level:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderBook:
    bids: list[Level]
    asks: list[Level]

    @property
    def best_bid(self) -> Decimal | None:
        return max((b.price for b in self.bids), default=None)

    @property
    def best_ask(self) -> Decimal | None:
        return min((a.price for a in self.asks), default=None)


@dataclass(frozen=True)
class Fill:
    side: Side
    requested_qty: Decimal
    filled_qty: Decimal
    fill_price: Decimal
    vwap: Decimal
    impact: Decimal
    used_fallback: bool
    band_capped: bool
    mark: Decimal
    best_bid: Decimal | None
    best_ask: Decimal | None
    mid: Decimal | None
    spread: Decimal | None
    slippage_vs_mark_bps: Decimal
    slippage_vs_mid_bps: Decimal | None
    consumed: list[tuple[str, str]] = field(default_factory=list)


def impact_fraction(
    notional: Decimal, vol_24h: Decimal | None, k: Decimal, illiquid_floor: Decimal
) -> Decimal:
    """Linear-impact fraction `k·notional/vol_24h`, capped at `illiquid_floor`.

    Impact is opt-in: with `k == 0` there is no overlay (fill = VWAP). When
    modelling impact (`k > 0`) but 24h volume is unknown/zero, the book is treated
    as illiquid and the floor is applied. `k` is a seed for the trained model.
    """
    if k <= 0:
        return Decimal(0)
    if vol_24h is None or vol_24h <= 0:
        return illiquid_floor
    frac = k * notional / vol_24h
    return max(Decimal(0), min(frac, illiquid_floor))


def _walk(
    levels: list[Level], qty: Decimal, band: Decimal | None, side: Side
) -> tuple[Decimal, Decimal | None, list[tuple[str, str]], bool]:
    """Consume `levels` (in walk order) up to `qty`, stopping at the band.

    Returns (filled_qty, vwap_or_None, consumed, band_hit).
    """
    remaining = qty
    cost = Decimal(0)
    consumed: list[tuple[str, str]] = []
    band_hit = False
    for lvl in levels:
        if remaining <= 0:
            break
        if band is not None and (
            (side == "buy" and lvl.price > band)
            or (side == "sell" and lvl.price < band)
        ):
            band_hit = True
            break
        take = min(lvl.size, remaining)
        cost += lvl.price * take
        consumed.append((str(lvl.price), str(take)))
        remaining -= take
    filled = qty - remaining
    vwap = (cost / filled) if filled > 0 else None
    return filled, vwap, consumed, band_hit


def compute_fill(
    side: Side,
    qty: Decimal,
    book: OrderBook,
    *,
    mark: Decimal,
    tick_size: Decimal,
    band: Decimal | None = None,
    slippage_ticks: Decimal = Decimal(0),
    contract_value: Decimal = Decimal(1),
    vol_24h: Decimal | None = None,
    k: Decimal = Decimal(0),
    illiquid_floor: Decimal = Decimal("0.02"),
) -> Fill:
    """Simulate a paper fill of `qty` against `book`. See module docstring."""
    levels = (
        sorted(book.asks, key=lambda x: x.price)
        if side == "buy"
        else sorted(book.bids, key=lambda x: x.price, reverse=True)
    )
    best_bid, best_ask = book.best_bid, book.best_ask
    mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None

    filled, vwap, consumed, band_hit = _walk(levels, qty, band, side)

    if filled <= 0 or vwap is None:
        # Thin/empty book (or fully outside band) -> synthetic fill off the mark.
        half = (spread / 2) if spread is not None else Decimal(0)
        slip = slippage_ticks * tick_size
        price = mark + half + slip if side == "buy" else mark - half - slip
        if band is not None:
            price = min(price, band) if side == "buy" else max(price, band)
        return _build(side, qty, qty, price, price, Decimal(0), True, band_hit,
                      mark, best_bid, best_ask, mid, spread, consumed)

    # Linear-impact overlay on the VWAP, clamped to the band.
    notional = vwap * filled * contract_value
    impact = vwap * impact_fraction(notional, vol_24h, k, illiquid_floor)
    price = vwap + impact if side == "buy" else vwap - impact
    if band is not None:
        price = min(price, band) if side == "buy" else max(price, band)
    band_capped = band_hit and filled < qty
    return _build(side, qty, filled, price, vwap, impact, False, band_capped,
                  mark, best_bid, best_ask, mid, spread, consumed)


def _bps(num: Decimal, ref: Decimal) -> Decimal:
    return (num / ref) * Decimal(10_000) if ref else Decimal(0)


def _build(
    side: Side, requested: Decimal, filled: Decimal, fill_price: Decimal,
    vwap: Decimal, impact: Decimal, used_fallback: bool, band_capped: bool,
    mark: Decimal, best_bid: Decimal | None, best_ask: Decimal | None,
    mid: Decimal | None, spread: Decimal | None, consumed: list[tuple[str, str]],
) -> Fill:
    return Fill(
        side=side,
        requested_qty=requested,
        filled_qty=filled,
        fill_price=fill_price,
        vwap=vwap,
        impact=impact,
        used_fallback=used_fallback,
        band_capped=band_capped,
        mark=mark,
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread=spread,
        slippage_vs_mark_bps=_bps(fill_price - mark, mark),
        slippage_vs_mid_bps=_bps(fill_price - mid, mid) if mid is not None else None,
        consumed=consumed,
    )
