"""Expiry lifecycle: settlement-time filtering + symbol-universe diffing.

Delta BTC/ETH options settle at 12:00 UTC (17:30 IST). After that instant an
expiry is dead and must vanish from the chooser; new expiries that Delta lists
intraday must appear once the ingestor re-seeds. These pure helpers drive both.
"""

from datetime import date, datetime, timezone

from app.services.chain import (
    default_expiry,
    is_expiry_live,
    live_expiries,
    symbol_diff,
)

UTC = timezone.utc


# ---- is_expiry_live ----------------------------------------------------------

def test_expiry_live_before_settlement() -> None:
    # 11:59 UTC on expiry day — still trading
    now = datetime(2026, 6, 12, 11, 59, tzinfo=UTC)
    assert is_expiry_live(date(2026, 6, 12), now) is True


def test_expiry_dead_at_settlement_instant() -> None:
    # 12:00:00 UTC exactly — settled
    now = datetime(2026, 6, 12, 12, 0, 0, tzinfo=UTC)
    assert is_expiry_live(date(2026, 6, 12), now) is False


def test_expiry_dead_after_settlement() -> None:
    # 12:01 UTC == 17:31 IST — the case the user hit at "5:30 pm"
    now = datetime(2026, 6, 12, 12, 1, tzinfo=UTC)
    assert is_expiry_live(date(2026, 6, 12), now) is False


def test_future_expiry_is_live() -> None:
    now = datetime(2026, 6, 12, 12, 1, tzinfo=UTC)
    assert is_expiry_live(date(2026, 6, 15), now) is True


def test_past_expiry_is_dead() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    assert is_expiry_live(date(2026, 6, 11), now) is False


# ---- live_expiries -----------------------------------------------------------

def test_live_expiries_drops_settled_keeps_order() -> None:
    expiries = [date(2026, 6, 11), date(2026, 6, 12), date(2026, 6, 15), date(2026, 6, 19)]
    # 17:31 IST on the 12th: 11th and 12th are dead, 15th + 19th live
    now = datetime(2026, 6, 12, 12, 1, tzinfo=UTC)
    assert live_expiries(expiries, now) == [date(2026, 6, 15), date(2026, 6, 19)]


def test_live_expiries_keeps_todays_expiry_before_settlement() -> None:
    expiries = [date(2026, 6, 12), date(2026, 6, 15)]
    now = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)  # 14:30 IST, before settle
    assert live_expiries(expiries, now) == [date(2026, 6, 12), date(2026, 6, 15)]


# ---- default_expiry (settlement-aware) --------------------------------------

def test_default_expiry_skips_settled_today() -> None:
    expiries = [date(2026, 6, 12), date(2026, 6, 15), date(2026, 6, 19)]
    now = datetime(2026, 6, 12, 12, 1, tzinfo=UTC)  # after 17:30 IST
    # must jump to the 15th, never return the dead 12th
    assert default_expiry(expiries, now) == date(2026, 6, 15)


def test_default_expiry_picks_today_before_settlement() -> None:
    expiries = [date(2026, 6, 12), date(2026, 6, 15)]
    now = datetime(2026, 6, 12, 8, 0, tzinfo=UTC)
    assert default_expiry(expiries, now) == date(2026, 6, 12)


def test_default_expiry_none_when_empty() -> None:
    assert default_expiry([], datetime(2026, 6, 12, 8, 0, tzinfo=UTC)) is None


def test_default_expiry_falls_back_to_latest_when_all_settled() -> None:
    # degenerate: every listed expiry already settled -> latest (furthest) date
    expiries = [date(2026, 6, 10), date(2026, 6, 11)]
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    assert default_expiry(expiries, now) == date(2026, 6, 11)


# ---- symbol_diff -------------------------------------------------------------

def test_symbol_diff_new_and_gone() -> None:
    current = {"C-BTC-60000-120626", "P-BTC-60000-120626"}
    fresh = {"P-BTC-60000-120626", "C-BTC-65000-150626"}
    new, gone = symbol_diff(current, fresh)
    assert new == {"C-BTC-65000-150626"}
    assert gone == {"C-BTC-60000-120626"}


def test_symbol_diff_no_change() -> None:
    syms = {"C-BTC-60000-120626"}
    new, gone = symbol_diff(syms, set(syms))
    assert new == set()
    assert gone == set()
