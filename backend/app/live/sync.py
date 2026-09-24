"""Live sync loop: mirror each user's real Delta positions and protect them.

Every 1s per user holding a trade key:
- every POLL_EVERY: pull positions from Delta; group option legs by (underlying, expiry)
  into `Position(source='live')` rows; legs that went flat on Delta are closed here (with
  the real fill). If the fill came from OUR native stop, the rest of the group is exited
  (user rule: any leg SL => exit all). A leg the user closed by hand does not.
- armed groups (`auto_exit`): evaluate leg + basket SL on live marks via the shared paper
  engine (sim/exit_engine.py); a hit fires ONCE (`exiting`) and hands off to executor.
- every GUARD_EVERY: keep one reduce-only stop-market resting on Delta per armed leg;
  refresh wallet equity, margin usage and the SL-vs-liquidation verdict; raise alerts.

The paper ticker never mutates live rows (it only samples them for the charts).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.vault import get_secret
from app.db.models import Leg, Log, Position, UserSecret
from app.db.session import SessionLocal
from app.engines.money import leg_pnl
from app.live import journal, risk
from app.live.alerts import AlertBook, send_telegram
from app.live.client import DeltaError, LiveClient, OrderRefused
from app.live.executor import ExitTarget, exit_group, real_sizes
from app.services.chain import Contract, normalize_contract
from app.sim.exit_engine import CombinedStop, ExitDecision, ExitLeg, combined_floor, evaluate_exit
from app.sim.marketview import MarketView

log = logging.getLogger("live_sync")

POLL_EVERY = 2.0
GUARD_EVERY = 10.0
USERS_EVERY = 30.0
DELTA_DOWN_AFTER = 10.0
STALE_GRACE_S = 30.0
LEG_STALE_S = 10.0  # one option's mark older than this counts as stale even if the feed is up


# --------------------------------------------------------------------------- #
# pure pieces (unit-tested)
# --------------------------------------------------------------------------- #
@dataclass
class Reconciled:
    new_groups: list[Position]
    gone: list[tuple[Position, Leg]]  # legs flat on Delta, still open here
    untracked: list[str]  # non-option / unknown symbols


def reconcile(
    uid: uuid.UUID,
    groups: list[Position],
    delta_positions: list[dict[str, Any]],
    contract_of: Callable[[str], Contract | None],
    spot_of: Callable[[str], float],
    now: datetime,
) -> Reconciled:
    """Upsert Delta's open option positions into live groups (mutates `groups`' legs).

    A group that is `exiting` never takes on new legs: a position (re)opened after its SL
    fired starts a fresh, disarmed group instead of silently joining one whose SL is spent.
    """
    open_groups = [p for p in groups if p.status == "open"]
    by_key = {(p.underlying, p.expiry): p for p in open_groups if not p.exiting}
    open_legs = {
        lg.product_id: (p, lg) for p in open_groups for lg in p.legs if lg.status == "open"
    }
    seen: set[int] = set()
    flipped: set[int] = set()
    out = Reconciled([], [], [])
    for dp in delta_positions:
        size = int(dp.get("size") or 0)
        if size == 0:
            continue
        symbol = str(dp.get("product_symbol") or "")
        raw_pid = int(dp.get("product_id") or 0)
        # Anything Delta says is open stays open here, even if the feed can't describe it
        # right now — never close (and silently disarm) a leg because of a feed gap.
        seen.add(raw_pid)
        c = contract_of(symbol)
        if c is None or c.expiry is None or c.product_id is None:
            if raw_pid not in open_legs:
                out.untracked.append(symbol)
            continue
        pid = raw_pid or int(c.product_id)
        seen.add(pid)
        underlying = symbol.split("-")[1] if symbol.count("-") >= 3 else ""
        key = (underlying, c.expiry.isoformat())
        side = "sell" if size < 0 else "buy"
        entry = float(dp.get("entry_price") or c.mark_price or 0)
        hit = open_legs.get(pid)
        if hit is not None and hit[1].side == side:
            if not hit[0].exiting:  # an exit in flight re-reads real sizes itself
                hit[1].qty, hit[1].entry = float(abs(size)), entry
            continue
        if hit is not None:  # flipped long<->short: the old leg is closed, a new one opens
            out.gone.append(hit)
            flipped.add(pid)
        pos = by_key.get(key)
        if pos is None:
            pos = _new_group(uid, underlying, key[1], now)
            by_key[key] = pos
            out.new_groups.append(pos)
        pos.legs.append(
            Leg(
                user_id=uid,
                symbol=symbol,
                product_id=pid,
                underlying=underlying,
                type=c.option_type,
                strike=c.strike,
                contract_value=c.contract_value or 0.001,
                expiry=key[1],
                dte=max((c.expiry - now.date()).days, 0),
                side=side,
                qty=float(abs(size)),
                entry=entry,
                mark_at_entry=c.mark_price or entry,
                spot_at_entry=spot_of(underlying),
                auto_exit=False,
                close_scope="strategy",
                status="open",
            )
        )
    for pid, (pos, lg) in open_legs.items():
        if pid not in seen and pid not in flipped:
            out.gone.append((pos, lg))
    return out


def _new_group(uid: uuid.UUID, underlying: str, expiry: str, now: datetime) -> Position:
    return Position(
        user_id=uid,
        name=f"{underlying} {expiry} (live)",
        underlying=underlying,
        expiry=expiry,
        margin=0.0,
        entry_margin=0.0,
        margin_badge="est",
        opened_at=now,
        status="open",
        auto_exit=False,  # tracking only until the user arms it
        auto_exit_suspended=False,
        stale_hard_stop=True,
        source="live",
        exiting=False,
        legs=[],
    )


def basket_floor(pos: Position) -> float | None:
    return combined_floor(
        CombinedStop(pos.stop_loss_amount, pos.stop_loss_pct_of_margin), pos.margin
    )


def basket_target(pos: Position) -> float | None:
    """Basket take-profit as a USD profit (amount, or % of margin)."""
    if pos.target_pnl is not None:
        return abs(pos.target_pnl)
    if pos.target_pct_of_margin is not None:
        return pos.margin * abs(pos.target_pct_of_margin) / 100
    return None


def desired_bracket(pos: Position, lg: Leg, tick: float) -> tuple[float | None, float | None]:
    """(SL, target) trigger prices for the leg's Delta bracket (None = that side unset)."""
    sl = risk.bracket_sl_price(
        lg.side, lg.entry, lg.qty, lg.contract_value, lg.sl_price, basket_floor(pos), tick
    )
    return sl, lg.tp_price


def evaluate_live(pos: Position, mark_of: Callable[[Leg], float], stale: bool, grace: bool) -> Any:
    """Shared paper engine with the live rules: leg SL/target are premium trigger prices on
    the mark, and ANY leg SL or target — or the basket SL/target — closes the WHOLE group.

    A trigger price maps exactly onto a P&L threshold (P&L is linear in the mark), so the
    engine's P&L comparison fires precisely when the mark crosses the price."""

    def at(lg: Leg, price: float | None) -> float | None:
        return (
            None if price is None else leg_pnl(lg.side, lg.qty, lg.contract_value, lg.entry, price)
        )

    legs = [
        ExitLeg(
            id=str(lg.id),
            pnl=leg_pnl(lg.side, lg.qty, lg.contract_value, lg.entry, mark_of(lg)),
            target_pnl=at(lg, lg.tp_price),
            stop_pnl=at(lg, lg.sl_price),
            auto_exit=True,
            close_scope="strategy",
            status=lg.status,
        )
        for lg in pos.legs
    ]
    net = sum(lp.pnl for lp in legs if lp.status == "open")
    d = evaluate_exit(
        legs,
        net_pnl=net,
        margin=pos.margin,
        combined_stop=CombinedStop(pos.stop_loss_amount, pos.stop_loss_pct_of_margin),
        combined_auto_exit=True,
        stale=stale,
        stale_hard_stop=True,
        stale_grace_elapsed=grace,
    )
    target = basket_target(pos)
    if d.kind == "none" and not stale and target is not None and net >= target:
        return ExitDecision("close-strategy", reason="basket target")
    if d.reason.startswith("leg TP/SL"):  # say which: an SL (loss) or a target (profit)
        hit_sl = any(
            risk.sl_crossed(lg.side, mark_of(lg), lg.sl_price)
            for lg in pos.legs
            if lg.status == "open"
        )
        return ExitDecision(
            d.kind, reason=d.reason.replace("leg TP/SL", "leg SL" if hit_sl else "leg target")
        )
    return d


def risk_legs(
    group: Position, others: list[Position], mark_of: Callable[[Leg], float]
) -> list[risk.RiskLeg]:
    def rl(lg: Leg, mine: bool) -> risk.RiskLeg:
        return risk.RiskLeg(
            type=lg.type,
            side=lg.side,
            qty=lg.qty,
            strike=lg.strike,
            dte_days=max(lg.dte, 1 / 24),
            cv=lg.contract_value,
            entry=lg.entry,
            mark=mark_of(lg),
            sl_price=lg.sl_price if mine else None,
            in_group=mine,
        )

    out = [rl(lg, True) for lg in group.legs if lg.status == "open"]
    for o in others:
        if o.id != group.id and o.underlying == group.underlying:
            out += [rl(lg, False) for lg in o.legs if lg.status == "open"]
    return out


def usd_wallet(wallet: list[dict[str, Any]]) -> dict[str, float] | None:
    """USD (or USDT) wallet: total balance and what's free for new margin."""
    for asset in ("USD", "USDT"):
        for w in wallet:
            if w.get("asset_symbol") == asset:
                bal = float(w.get("balance") or 0)
                avail = w.get("available_balance")
                return {"balance": bal, "available": float(avail) if avail is not None else bal}
    return None


def usd_balance(wallet: list[dict[str, Any]]) -> float | None:
    for asset in ("USD", "USDT"):
        for w in wallet:
            if w.get("asset_symbol") == asset:
                return float(w.get("balance") or 0)
    return None


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
class LiveSync:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.alerts = AlertBook()
        # user -> position id -> LiqCheck (for the UI; never served across users)
        self.liq: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}
        self.usage: dict[uuid.UUID, float | None] = {}
        self._clients: dict[uuid.UUID, tuple[str, LiveClient]] = {}
        self._users: list[uuid.UUID] = []
        self._last_users = 0.0
        self._last_poll: dict[uuid.UUID, float] = {}
        self._last_guard: dict[uuid.UUID, float] = {}
        self._last_ok: dict[uuid.UUID, float] = {}
        self._exits: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._task: asyncio.Task[None] | None = None
        app.state.live = self

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        for t in [self._task, *self._exits.values()]:
            if t:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        for _, c in self._clients.values():
            await c.aclose()

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("live tick error: %s", e)
            await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        mono = time.monotonic()
        if mono - self._last_users >= USERS_EVERY:
            self._last_users = mono
            async with SessionLocal() as s:
                res = await s.execute(
                    select(UserSecret.user_id).where(UserSecret.kind == "delta_trade_secret")
                )
                self._users = list(set(res.scalars().all()))
        for uid in self._users:
            try:
                await self._tick_user(uid, mono)
            except Exception as e:  # noqa: BLE001
                log.warning("live user tick error: %s", e)

    async def client_for(self, session: AsyncSession, uid: uuid.UUID) -> LiveClient | None:
        key = await get_secret(session, uid, "delta_trade_key")
        secret = await get_secret(session, uid, "delta_trade_secret")
        if not key or not secret:
            return None
        cached = self._clients.get(uid)
        if cached and cached[0] == key:
            return cached[1]
        if cached:
            await cached[1].aclose()
        c = LiveClient(key, secret)
        self._clients[uid] = (key, c)
        return c

    async def _tick_user(self, uid: uuid.UUID, mono: float) -> None:
        mv = MarketView(self.app.state.market)
        async with SessionLocal() as s:
            client = await self.client_for(s, uid)
            if client is None:
                return
            groups = await self._groups(s, uid)
            if mono - self._last_poll.get(uid, 0) >= POLL_EVERY:
                self._last_poll[uid] = mono
                try:
                    delta_positions = await client.positions()
                    self._last_ok[uid] = mono
                    self.alerts.clear(uid, "delta-down")
                except Exception as e:  # noqa: BLE001
                    delta_positions = None
                    if mono - self._last_ok.get(uid, mono) >= DELTA_DOWN_AFTER:
                        await self._alert(
                            s,
                            uid,
                            "delta-down",
                            "critical",
                            f"Can't reach Delta ({type(e).__name__}). "
                            "Native stops on Delta still protect open legs.",
                        )
                    self._last_ok.setdefault(uid, mono)
                if delta_positions is not None and self.app.state.market.tickers:
                    await self._reconcile(s, uid, client, mv, groups, delta_positions)
                    groups = [g for g in groups if g.status == "open"]

            for p in groups:
                await self._maybe_trigger(s, uid, client, mv, p)

            if mono - self._last_guard.get(uid, 0) >= GUARD_EVERY:
                self._last_guard[uid] = mono
                await self._guard(s, uid, client, mv, groups)
            await s.commit()

    async def _groups(self, s: AsyncSession, uid: uuid.UUID) -> list[Position]:
        res = await s.execute(
            select(Position)
            .where(Position.user_id == uid, Position.source == "live", Position.status == "open")
            .options(selectinload(Position.legs))
        )
        return list(res.scalars().all())

    # ---- reconcile ------------------------------------------------------ #
    async def _reconcile(
        self,
        s: AsyncSession,
        uid: uuid.UUID,
        client: LiveClient,
        mv: MarketView,
        groups: list[Position],
        delta_positions: list[dict[str, Any]],
    ) -> None:
        now = datetime.now(UTC)
        tickers = self.app.state.market.tickers
        r = reconcile(
            uid,
            groups,
            delta_positions,
            lambda sym: normalize_contract(tickers[sym]) if sym in tickers else None,
            mv.spot,
            now,
        )
        for g in r.new_groups:
            s.add(g)
            groups.append(g)
            await self._journal(s, uid, "LIVE", f"tracking {g.name}", "info")
        for dp in delta_positions:
            sym = str(dp.get("product_symbol") or "")
            if sym not in r.untracked:
                self.alerts.clear(uid, f"untracked:{sym}")
        for sym in r.untracked:
            await self._alert(
                s,
                uid,
                f"untracked:{sym}",
                "warning",
                f"{sym} is open on Delta but not tracked (options only); "
                "it is not in any SL or liquidation estimate.",
            )
        if r.gone:
            await self._close_legs(s, uid, client, mv, r.gone, now)

    async def _close_legs(
        self,
        s: AsyncSession,
        uid: uuid.UUID,
        client: LiveClient,
        mv: MarketView,
        gone: list[tuple[Position, Leg]],
        now: datetime,
    ) -> None:
        """Record legs Delta reports flat (real fill price/fee), close emptied groups, and
        cascade: a leg closed by OUR native stop exits the rest of an armed group."""
        fills: dict[int, dict[str, Any]] = {}
        with contextlib.suppress(Exception):
            for fill in await client.fills([lg.product_id for _, lg in gone]):
                fills.setdefault(int(fill.get("product_id") or 0), fill)  # newest first
        stop_fired: dict[uuid.UUID, str] = {}  # group -> which Delta bracket side fired
        for pos, lg in gone:
            f = fills.get(lg.product_id)
            px = float(f["price"]) if f and f.get("price") else _mark(mv, lg)
            oid = int(f.get("order_id") or 0) if f else 0
            by_sl = bool(oid and oid == lg.stop_order_id)
            by_tp = bool(oid and oid == lg.tp_order_id)
            by_stop = by_sl or by_tp  # our Delta bracket closed it (SL or target)
            lg.status = "closed"
            lg.exit_price = px
            lg.exit_at = now
            lg.exit_reason = (
                "Delta bracket SL"
                if by_sl
                else "Delta bracket target"
                if by_tp
                else "exit all"
                if pos.exiting
                else "closed on Delta"
            )
            lg.exit_gross = leg_pnl(lg.side, lg.qty, lg.contract_value, lg.entry, px)
            lg.exit_fees = float(f.get("commission") or 0) if f else 0.0
            lg.stop_order_id = lg.tp_order_id = None
            await self._journal(
                s,
                uid,
                "LIVE",
                f"{pos.name}: {lg.symbol} closed @ {px} ({lg.exit_reason})",
                "warn" if by_stop else "info",
            )
            if by_stop and pos.auto_exit:
                stop_fired[pos.id] = lg.exit_reason
        for pos in {p.id: p for p, _ in gone}.values():
            if not any(lg.status == "open" for lg in pos.legs):
                pos.status, pos.closed_at = "closed", now
                pos.close_reason = pos.close_reason or (
                    "exit all" if pos.exiting else "closed on Delta"
                )
                ring = getattr(self.app.state, "sim_series", {}).get(str(pos.id))
                pos.summary = journal.summarize(pos, await journal.load_samples(s, pos, ring), now)
                await self._journal(
                    s,
                    uid,
                    "LIVE_CLOSE",
                    f"{pos.name}: closed · P&L ${pos.summary['pnl']:,.2f} · "
                    f"max ${pos.summary['maxMtm'] or 0:,.2f} · "
                    f"min ${pos.summary['minMtm'] or 0:,.2f} · "
                    f"max DD ${pos.summary['maxDrawdown']:,.2f} · {pos.close_reason}",
                    "info",
                )
                self.liq.get(uid, {}).pop(str(pos.id), None)
                self.alerts.clear(uid, f"exit:{pos.id}")
            elif pos.id in stop_fired:
                await self._start_exit(s, uid, client, mv, pos, stop_fired[pos.id])

    # ---- SL trigger ------------------------------------------------------ #
    async def _maybe_trigger(
        self, s: AsyncSession, uid: uuid.UUID, client: LiveClient, mv: MarketView, p: Position
    ) -> None:
        if not p.auto_exit or p.exiting:
            return
        age = mv.m.feed_status().get("age_seconds")
        grace = isinstance(age, (int, float)) and age > STALE_GRACE_S
        ages = [mv.mark_age(lg.symbol) for lg in p.legs if lg.status == "open"]
        # stale = whole feed down OR any leg's own mark frozen (never trust one dead symbol)
        leg_stale = any(a is None or a > LEG_STALE_S for a in ages)
        grace = grace or any(a is not None and a > STALE_GRACE_S for a in ages)
        d = evaluate_live(
            p, lambda lg: _mark(mv, lg), stale=not mv.fresh() or leg_stale, grace=grace
        )
        p.auto_exit_suspended = d.kind == "suspended"
        if d.kind in ("close-strategy", "close-legs"):
            await self._start_exit(s, uid, client, mv, p, d.reason)

    async def _start_exit(
        self,
        s: AsyncSession,
        uid: uuid.UUID,
        client: LiveClient,
        mv: MarketView,
        p: Position,
        reason: str,
        *,
        manual: bool = False,
    ) -> bool:
        if p.id in self._exits and not self._exits[p.id].done():
            return False
        p.exiting = True
        # a target (leg or basket) is a profit exit, not an SL — label it so in the journal
        kind = "Target" if "target" in reason.lower() else "SL"
        # column is VARCHAR(40): an over-long label must never block the exit it describes
        p.close_reason = ("manual exit" if manual else f"{kind}: {reason}")[:40]
        await s.commit()  # persist the one-shot flag before any order goes out
        emergency = risk.margin_level(self.usage.get(uid)) == "emergency"
        targets = [
            ExitTarget(
                lg.product_id,
                lg.symbol,
                _tick(self.app.state.market, lg.symbol),
                lg.stop_order_id,
                lg.tp_order_id,
            )
            for lg in p.legs
            if lg.status == "open"
        ]
        await self._alert(
            s,
            uid,
            f"exit:{p.id}",
            "critical",
            f"{p.name}: exiting ALL legs — {p.close_reason}"
            + (" (near liquidation: market orders)" if emergency else ""),
        )
        self._exits[p.id] = asyncio.create_task(
            self._run_exit(uid, client, mv, p.id, p.name, targets, emergency)
        )
        return True

    async def _run_exit(
        self,
        uid: uuid.UUID,
        client: LiveClient,
        mv: MarketView,
        pid: uuid.UUID,
        name: str,
        targets: list[ExitTarget],
        emergency: bool,
    ) -> None:
        syms = {t.symbol: t for t in targets}

        async def note(level: str, msg: str) -> None:
            async with SessionLocal() as s:
                await self._journal(
                    s, uid, "LIVE_EXIT", f"{name}: {msg}", "warn" if level != "info" else "info"
                )
                await s.commit()

        def mark_of(sym: str) -> float:
            q = mv.quote(sym)
            return q.mark if q and q.mark is not None else 0.0

        res = await exit_group(client, list(syms.values()), mark_of, note, emergency=emergency)
        async with SessionLocal() as s:
            if res.flat:
                # Delta confirmed flat: record it now, so a position re-opened on the same
                # strikes before the next sync starts a new group instead of joining this one
                q = await s.execute(
                    select(Position).where(Position.id == pid).options(selectinload(Position.legs))
                )
                p = q.scalar_one_or_none()
                if p is not None and p.status == "open":
                    gone = [(p, lg) for lg in p.legs if lg.status == "open" and lg.symbol in syms]
                    await self._close_legs(s, uid, client, mv, gone, datetime.now(UTC))
                await self._alert(
                    s,
                    uid,
                    f"exit:{pid}",
                    "warning",
                    f"{name}: all legs closed on Delta ({res.orders} orders).",
                    sticky=False,
                )
            elif res.refused:
                await self._alert(
                    s,
                    uid,
                    f"exit:{pid}",
                    "emergency",
                    f"{name}: SL hit but orders are BLOCKED ({res.refused}). "
                    "Close manually on Delta now.",
                )
            else:
                await self._alert(
                    s,
                    uid,
                    f"exit:{pid}",
                    "emergency",
                    f"{name}: NOT FLAT after 30s of retries. Close manually on Delta now.",
                )
            await s.commit()

    async def manual_exit(self, s: AsyncSession, uid: uuid.UUID, pid: uuid.UUID) -> bool:
        res = await s.execute(
            select(Position)
            .where(
                Position.id == pid,
                Position.user_id == uid,
                Position.source == "live",
                Position.status == "open",
            )
            .options(selectinload(Position.legs))
        )
        p = res.scalar_one_or_none()
        client = await self.client_for(s, uid)
        if p is None or client is None:
            return False
        return await self._start_exit(
            s, uid, client, MarketView(self.app.state.market), p, "manual", manual=True
        )

    # ---- guard: native stops + liquidation + usage ------------------------ #
    async def _guard(
        self,
        s: AsyncSession,
        uid: uuid.UUID,
        client: LiveClient,
        mv: MarketView,
        groups: list[Position],
    ) -> None:
        await self.sync_brackets(s, uid, client, groups)
        try:
            balance = usd_balance(await client.wallet())
        except Exception:  # noqa: BLE001
            balance = None
        if balance is None:
            await self._alert(
                s,
                uid,
                "wallet",
                "warning",
                "Couldn't read the Delta wallet — liquidation check paused.",
            )
            return
        self.alerts.clear(uid, "wallet")
        mark_of = lambda lg: _mark(mv, lg)  # noqa: E731
        worst_usage: float | None = None
        for g in groups:
            if not any(lg.status == "open" for lg in g.legs):
                continue
            spot = mv.spot(g.underlying)
            legs = risk_legs(g, groups, mark_of)
            g.margin = g.entry_margin = _group_im(g, spot, mark_of)
            chk = await asyncio.to_thread(
                risk.check_sl_vs_liquidation,
                legs,
                spot=spot,
                balance=balance,
                basket_floor=basket_floor(g),
            )
            self.liq.setdefault(uid, {})[str(g.id)] = asdict(chk)
            if chk.usage is not None:
                worst_usage = max(worst_usage or 0.0, chk.usage)
            if g.auto_exit and chk.verdict == "red":
                await self._alert(
                    s,
                    uid,
                    f"liq:{g.id}",
                    "critical",
                    f"{g.name}: liquidation can come BEFORE your SL — {chk.reason}. "
                    "Cut size, add capital or tighten the SL.",
                )
            else:
                self.alerts.clear(uid, f"liq:{g.id}")
        self.usage[uid] = worst_usage
        level = risk.margin_level(worst_usage)
        if level:
            await self._alert(
                s,
                uid,
                "usage",
                level,
                f"Margin usage {worst_usage:.0%} of equity "
                f"(liquidation at 100%). Balance ${balance:,.2f}.",
            )
        else:
            self.alerts.clear(uid, "usage")

    async def sync_brackets(
        self, s: AsyncSession, uid: uuid.UUID, client: LiveClient, groups: list[Position]
    ) -> None:
        """One Delta position bracket (SL + target) per leg of an armed group; none for
        disarmed ones. Only orders this app placed (ids stored on the leg) are ever replaced
        or cancelled — a bracket you set by hand on Delta is left alone (and Delta will then
        refuse ours, which alerts)."""
        try:
            resting = await client.open_stop_orders()
            sizes = await real_sizes(client)
        except Exception:  # noqa: BLE001
            return
        market = self.app.state.market
        for g in groups:
            if g.exiting:
                continue  # brackets stay resting until each leg is confirmed flat
            for lg in g.legs:
                if lg.status != "open":
                    continue
                if sizes.get(lg.product_id, 0) == 0:
                    # flat on Delta (e.g. its bracket just fired): the reconcile records the
                    # fill and cascades; re-placing here would orphan the fill's order id
                    continue
                tick = _tick(market, lg.symbol)
                want_sl, want_tp = desired_bracket(g, lg, tick) if g.auto_exit else (None, None)
                mine_ids = {i for i in (lg.stop_order_id, lg.tp_order_id) if i}
                mine = [o for o in resting if int(o.get("id") or 0) in mine_ids]
                have_sl = next(
                    (o for o in mine if o.get("stop_order_type") == "stop_loss_order"), None
                )
                have_tp = next(
                    (o for o in mine if o.get("stop_order_type") == "take_profit_order"), None
                )
                if mine_ids and not mine and g.auto_exit:
                    await self._alert(
                        s,
                        uid,
                        f"stop:{lg.id}",
                        "critical",
                        f"{g.name}: Delta bracket for {lg.symbol} disappeared on Delta — "
                        "re-placing. (Delta cancels all orders in liquidation.)",
                    )
                if _same(have_sl, want_sl) and _same(have_tp, want_tp):
                    if mine:
                        self.alerts.clear(uid, f"stop:{lg.id}")
                    continue
                for o in mine:
                    with contextlib.suppress(DeltaError):
                        await client.cancel(lg.product_id, int(o["id"]))
                lg.stop_order_id = lg.tp_order_id = lg.stop_price = None
                if want_sl is None and want_tp is None:
                    continue
                foreign = {
                    int(o.get("id") or 0) for o in resting if o.get("product_id") == lg.product_id
                }
                try:
                    kind = await client.place_bracket(
                        lg.product_id, lg.symbol, risk.close_side(lg.side), want_sl, want_tp, tick
                    )
                    new = [
                        o
                        for o in await client.open_stop_orders()
                        if o.get("product_id") == lg.product_id
                        and int(o.get("id") or 0) not in foreign
                    ]
                    for o in new:
                        if o.get("stop_order_type") == "stop_loss_order":
                            lg.stop_order_id = int(o["id"])
                        elif o.get("stop_order_type") == "take_profit_order":
                            lg.tp_order_id = int(o["id"])
                    lg.stop_price = want_sl
                    self.alerts.clear(uid, f"stop:{lg.id}")
                    await self._journal(
                        s,
                        uid,
                        "LIVE_STOP",
                        f"{g.name}: {lg.symbol} Delta bracket SL {want_sl} / target {want_tp} "
                        f"({kind} orders, mark trigger)",
                        "info" if kind == "market" else "warn",
                    )
                except OrderRefused as e:
                    await self._alert(
                        s,
                        uid,
                        "kill-switch",
                        "warning",
                        f"Delta brackets NOT placed: {e}. Nothing protects armed legs "
                        "on Delta while the server is down.",
                    )
                    return
                except DeltaError as e:
                    await self._alert(
                        s,
                        uid,
                        f"stop:{lg.id}",
                        "critical",
                        f"{g.name}: Delta rejected the bracket for {lg.symbol} ({e.explain()}). "
                        "Leg has NO exchange-side SL/target.",
                    )

    # ---- journal / alerts ------------------------------------------------- #
    async def _journal(
        self, s: AsyncSession, uid: uuid.UUID, action: str, detail: str, tone: str
    ) -> None:
        s.add(Log(user_id=uid, t=datetime.now(UTC), action=action, detail=detail[:2000], tone=tone))

    async def _alert(
        self,
        s: AsyncSession,
        uid: uuid.UUID,
        key: str,
        level: str,
        msg: str,
        *,
        sticky: bool = True,
    ) -> None:
        push = self.alerts.raise_(uid, key, level, msg, sticky=sticky)
        if push is None:
            return
        await self._journal(s, uid, "LIVE_ALERT", f"[{level}] {msg}", "warn")
        token = await get_secret(s, uid, "telegram_bot_token")
        chat = await get_secret(s, uid, "telegram_chat_id")
        if token and chat:
            sent, why = await send_telegram(
                self.app.state.http, token, chat, f"[{level.upper()}] {msg}"
            )
            if not sent:
                log.warning("telegram alert not delivered: %s", why)


def _same(order: dict[str, Any] | None, want: float | None) -> bool:
    """Resting order matches the wanted trigger (both absent counts as a match)."""
    if order is None or want is None:
        return order is None and want is None
    return abs(float(order.get("stop_price") or 0) - want) < 1e-9


def _mark(mv: MarketView, lg: Leg) -> float:
    q = mv.quote(lg.symbol)
    return q.mark if (q and q.mark is not None) else lg.entry  # real 0 = real loss


def _tick(market: Any, symbol: str) -> float:
    t = market.tickers.get(symbol) or {}
    try:
        return float(t.get("tick_size") or 0.1)
    except (TypeError, ValueError):
        return 0.1


def _group_im(g: Position, spot: float, mark_of: Callable[[Leg], float]) -> float:
    from app.engines.margin import MarginLeg, compute_margin, implied_vol

    legs = [
        MarginLeg(
            option_type=lg.type,
            side="long" if lg.side == "buy" else "short",
            qty=lg.qty,
            strike=lg.strike,
            dte_days=max(lg.dte, 1 / 24),
            iv=implied_vol(lg.type, spot, lg.strike, max(lg.dte, 1 / 24) / 365, mark_of(lg)) or 0.5,
            contract_value=lg.contract_value,
            mark=mark_of(lg),
        )
        for lg in g.legs
        if lg.status == "open"
    ]
    return compute_margin(legs, spot).initial_margin if legs and spot > 0 else 0.0
