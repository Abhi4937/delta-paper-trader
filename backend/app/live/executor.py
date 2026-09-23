"""Close a whole live group on Delta — the trigger has already fired; nothing re-triggers.

Loop until Delta reports every leg flat:
  1. re-read the REAL size of each leg from Delta (never trust our copy),
  2. shorts first (open-ended risk), then longs,
  3. reduce-only IOC limit at mark +- band, widening each round (CLOSE_BANDS), then market;
     `emergency` (margin usage near liquidation) skips straight to market.
Native stops stay resting until a leg is confirmed flat, so a crash mid-exit still leaves
the exchange-side backstop in place; both are reduce-only, so they can never overshoot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.live.client import DeltaError, LiveClient, OrderRefused
from app.live.risk import CLOSE_BANDS, Side, ioc_limit_price

MAX_ROUNDS = 30  # ~30s of retries, then give up and alert (a human must act)


@dataclass(frozen=True)
class ExitTarget:
    product_id: int
    symbol: str
    tick: float
    stop_order_id: int | None


@dataclass
class ExitResult:
    flat: bool
    refused: str | None = None
    orders: int = 0


Note = Callable[[str, str], Awaitable[None]]  # (level, message)


async def real_sizes(client: LiveClient) -> dict[int, int]:
    return {int(p["product_id"]): int(p.get("size") or 0) for p in await client.positions()}


async def exit_group(
    client: LiveClient,
    targets: list[ExitTarget],
    mark_of: Callable[[str], float],
    note: Note,
    *,
    emergency: bool = False,
    pause: float = 1.0,
) -> ExitResult:
    tries: dict[int, int] = {t.product_id: 0 for t in targets}
    res = ExitResult(flat=False)
    for _ in range(MAX_ROUNDS):
        sizes = await real_sizes(client)
        still = [t for t in targets if sizes.get(t.product_id, 0) != 0]
        if not still:
            res.flat = True
            break
        still.sort(key=lambda t: sizes[t.product_id] > 0)  # negative (short) sizes first
        for t in still:
            size = sizes[t.product_id]
            side: Side = "buy" if size < 0 else "sell"
            n = tries[t.product_id]
            tries[t.product_id] = n + 1
            try:
                if emergency or n >= len(CLOSE_BANDS):
                    await client.close_market(t.product_id, side, abs(size))
                    await note("warning", f"{t.symbol}: market {side} {abs(size)} (reduce-only)")
                else:
                    px = ioc_limit_price(side, mark_of(t.symbol), CLOSE_BANDS[n], t.tick)
                    await client.close_ioc(t.product_id, side, abs(size), px)
                    await note(
                        "info",
                        f"{t.symbol}: IOC {side} {abs(size)} @ {px} "
                        f"(band {CLOSE_BANDS[n]:.0%}, reduce-only)",
                    )
                res.orders += 1
            except OrderRefused as e:
                res.refused = str(e)
                await note("critical", f"{t.symbol}: order refused — {e}")
                return res
            except DeltaError as e:
                await note("warning", f"{t.symbol}: Delta rejected close ({e.code or e.status})")
        await asyncio.sleep(pause)

    if res.flat:
        for t in targets:
            if t.stop_order_id:
                try:
                    await client.cancel(t.product_id, t.stop_order_id)
                except DeltaError:
                    pass  # already gone with the position — nothing to protect
    return res
