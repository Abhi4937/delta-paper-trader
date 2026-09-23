"""Live alerts: in-memory per-user book (served on the /ws/state tick) + Telegram push.

A condition alert (margin usage, stop missing, sync down) stays until `clear`ed; an event
alert (exit done, order rejected) expires after EVENT_TTL. Telegram gets a message when an
alert is new, escalates (warning -> critical -> emergency), or is still active after
RESEND_EVERY — never more often. Messages carry only group names, numbers and actions.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("live_alerts")

LEVELS = ("info", "warning", "critical", "emergency")
RESEND_EVERY = 300.0
EVENT_TTL = 900.0


@dataclass
class Alert:
    key: str
    level: str
    message: str
    sticky: bool  # condition (until cleared) vs one-off event (expires)
    t: float = field(default_factory=time.time)
    sent_at: float = 0.0
    sent_level: str = ""


class AlertBook:
    def __init__(self) -> None:
        self._by_user: dict[uuid.UUID, dict[str, Alert]] = {}

    def raise_(
        self, uid: uuid.UUID, key: str, level: str, message: str, *, sticky: bool = True
    ) -> Alert | None:
        """Record an alert. Returns it when it should be pushed to Telegram now."""
        book = self._by_user.setdefault(uid, {})
        now = time.time()
        a = book.get(key)
        if a is None:
            a = book[key] = Alert(key, level, message, sticky, now)
        else:
            a.level, a.message, a.t, a.sticky = level, message, now, sticky
        escalated = LEVELS.index(level) > LEVELS.index(a.sent_level) if a.sent_level else True
        if escalated or now - a.sent_at >= RESEND_EVERY:
            a.sent_at, a.sent_level = now, level
            return a
        return None

    def clear(self, uid: uuid.UUID, key: str) -> None:
        self._by_user.get(uid, {}).pop(key, None)

    def active(self, uid: uuid.UUID) -> list[dict[str, Any]]:
        book = self._by_user.get(uid, {})
        now = time.time()
        for k in [k for k, a in book.items() if not a.sticky and now - a.t > EVENT_TTL]:
            del book[k]
        return [
            {"key": a.key, "level": a.level, "message": a.message, "t": int(a.t * 1000)}
            for a in sorted(book.values(), key=lambda a: -LEVELS.index(a.level))
        ]


async def send_telegram(
    http: httpx.AsyncClient, token: str, chat_id: str, text: str
) -> tuple[bool, str]:
    """(sent, reason). `reason` is Telegram's own description on failure, e.g.
    "Unauthorized" (bad/revoked token) or "Forbidden: bot can't initiate conversation"
    (Start not pressed)."""
    try:
        r = await http.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        # never log the URL — it contains the bot token
        log.warning("telegram send failed: %s", type(e).__name__)
        return False, f"couldn't reach Telegram ({type(e).__name__})"
    if r.status_code == 200:
        return True, ""
    try:
        return False, str(r.json().get("description") or f"HTTP {r.status_code}")
    except ValueError:
        return False, f"HTTP {r.status_code}"


def explain_telegram(reason: str) -> str:
    """Telegram's reason plus what to do about it."""
    low = reason.lower()
    if "chat not found" in low:
        return f"{reason} — check the chat id (from @userinfobot) and press Start in the bot"
    if "unauthorized" in low or "not found" in low:
        return f"{reason} — the bot token is wrong or revoked; paste the current one"
    if "initiate" in low or "blocked" in low:
        return f"{reason} — open the bot in Telegram and press Start"
    return reason
