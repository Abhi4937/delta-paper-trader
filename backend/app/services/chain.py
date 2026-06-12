"""Option-chain service: fetch + normalize Delta India option chains.

Normalizes the `/v2/tickers` option payload (fields confirmed against live data)
into typed rows for the chain grid: calls | strike | puts, with mark/bid/ask,
IV (bid/ask/mark), Greeks, and OI. Expiry is parsed from the contract symbol
(e.g. ``P-BTC-95000-310726`` -> 2026-07-31).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel

from app.delta.rest import DeltaRestClient

# Delta India BTC/ETH options settle at 12:00 UTC (17:30 IST). After this instant
# on its expiry day a contract is dead — it must drop out of the chooser, never be
# auto-selected, and never be traded. Mirrors the frontend `isLegExpired` cutoff.
SETTLEMENT_UTC_HOUR = 12


def _f(v: object) -> float | None:
    """Parse Delta's stringy numerics into float, tolerating None/''."""
    if v is None or v == "":
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_expiry_from_symbol(symbol: str) -> date | None:
    """``P-BTC-95000-310726`` -> date(2026, 7, 31). Returns None if unparseable."""
    parts = symbol.split("-")
    if len(parts) < 4:
        return None
    token = parts[-1]
    if len(token) != 6 or not token.isdigit():
        return None
    dd, mm, yy = int(token[0:2]), int(token[2:4]), int(token[4:6])
    try:
        return date(2000 + yy, mm, dd)
    except ValueError:
        return None


class Quote(BaseModel):
    bid: float | None = None
    ask: float | None = None
    bid_iv: float | None = None
    ask_iv: float | None = None
    mark_iv: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None


class Greeks(BaseModel):
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None


class Contract(BaseModel):
    symbol: str
    product_id: int | None = None
    option_type: str  # "call" | "put"
    strike: float
    mark_price: float | None = None
    last_price: float | None = None  # LTP (ticker close)
    oi: float | None = None
    oi_value_usd: float | None = None
    contract_value: float | None = None
    tick_size: float | None = None
    spot_price: float | None = None
    expiry: date | None = None
    quote: Quote
    greeks: Greeks


class ChainRow(BaseModel):
    strike: float
    call: Contract | None = None
    put: Contract | None = None


class OptionChain(BaseModel):
    underlying: str
    expiry: date | None = None
    spot: float | None = None
    atm_strike: float | None = None
    atm_iv: float | None = None
    rows: list[ChainRow]


_TYPE_MAP = {"call_options": "call", "put_options": "put"}


def normalize_contract(t: dict) -> Contract | None:
    """Normalize one `/v2/tickers` option element into a Contract."""
    opt = _TYPE_MAP.get(t.get("contract_type", ""))
    if opt is None:
        return None
    strike = _f(t.get("strike_price"))
    if strike is None:
        return None
    q = t.get("quotes") or {}
    g = t.get("greeks") or {}
    symbol = t.get("symbol", "")
    return Contract(
        symbol=symbol,
        product_id=t.get("product_id"),
        option_type=opt,
        strike=strike,
        mark_price=_f(t.get("mark_price")),
        last_price=_f(t.get("close")),
        oi=_f(t.get("oi")),
        oi_value_usd=_f(t.get("oi_value_usd")),
        contract_value=_f(t.get("contract_value")),
        tick_size=_f(t.get("tick_size")),
        spot_price=_f(t.get("spot_price")),
        expiry=parse_expiry_from_symbol(symbol),
        quote=Quote(
            bid=_f(q.get("best_bid")),
            ask=_f(q.get("best_ask")),
            bid_iv=_f(q.get("bid_iv")),
            ask_iv=_f(q.get("ask_iv")),
            mark_iv=_f(q.get("mark_iv")),
            bid_size=_f(q.get("bid_size")),
            ask_size=_f(q.get("ask_size")),
        ),
        greeks=Greeks(
            delta=_f(g.get("delta")),
            gamma=_f(g.get("gamma")),
            theta=_f(g.get("theta")),
            vega=_f(g.get("vega")),
            rho=_f(g.get("rho")),
        ),
    )


def build_chain(
    tickers: list[dict], underlying: str, expiry: date | None
) -> OptionChain:
    """Group normalized contracts into call|strike|put rows for one expiry."""
    contracts = [c for t in tickers if (c := normalize_contract(t)) is not None]
    if expiry is not None:
        contracts = [c for c in contracts if c.expiry == expiry]

    by_strike: dict[float, ChainRow] = {}
    spot: float | None = None
    for c in contracts:
        spot = spot or c.spot_price
        row = by_strike.setdefault(c.strike, ChainRow(strike=c.strike))
        if c.option_type == "call":
            row.call = c
        else:
            row.put = c

    rows = [by_strike[k] for k in sorted(by_strike)]

    atm_strike: float | None = None
    atm_iv: float | None = None
    if spot is not None and rows:
        atm_row = min(rows, key=lambda r: abs(r.strike - spot))
        atm_strike = atm_row.strike
        # ATM IV = average of the ATM call & put mark IVs. Delta does not publish an
        # ATM-IV formula (proprietary IV model); the call/put average is our chosen
        # convention — validate vs Delta's displayed ATM IV. Fall back to whichever
        # side has a quote.
        atm_ivs = [
            s.quote.mark_iv
            for s in (atm_row.call, atm_row.put)
            if s is not None and s.quote.mark_iv is not None
        ]
        if atm_ivs:
            atm_iv = sum(atm_ivs) / len(atm_ivs)

    return OptionChain(
        underlying=underlying,
        expiry=expiry,
        spot=spot,
        atm_strike=atm_strike,
        atm_iv=atm_iv,
        rows=rows,
    )


async def fetch_option_tickers(client: DeltaRestClient, underlying: str) -> list[dict]:
    """Fetch all call+put option tickers for an underlying (public endpoint)."""
    data = await client.get(
        "/v2/tickers",
        params={
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": underlying,
        },
    )
    return data.get("result", []) if isinstance(data, dict) else []


def list_expiries(tickers: list[dict]) -> list[date]:
    """Sorted unique expiries present in the option tickers."""
    seen: set[date] = set()
    for t in tickers:
        e = parse_expiry_from_symbol(t.get("symbol", ""))
        if e is not None:
            seen.add(e)
    return sorted(seen)


def expiry_settlement(e: date) -> datetime:
    """The UTC instant an expiry settles (12:00 UTC == 17:30 IST)."""
    return datetime(e.year, e.month, e.day, SETTLEMENT_UTC_HOUR, 0, tzinfo=UTC)


def is_expiry_live(e: date, now: datetime) -> bool:
    """True while the contract is still trading (before its settlement instant)."""
    return now < expiry_settlement(e)


def live_expiries(expiries: list[date], now: datetime | None = None) -> list[date]:
    """Drop expiries that have already settled; order preserved."""
    now = now or datetime.now(UTC)
    return [e for e in expiries if is_expiry_live(e, now)]


def default_expiry(expiries: list[date], now: datetime | None = None) -> date | None:
    """Nearest *live* expiry. Degenerate fallback (all settled): the furthest one."""
    if not expiries:
        return None
    live = live_expiries(expiries, now)
    return live[0] if live else expiries[-1]


def symbol_diff(current: set[str], fresh: set[str]) -> tuple[set[str], set[str]]:
    """(newly listed, no-longer listed) between the cached and freshly fetched sets."""
    return fresh - current, current - fresh
