"""
utils/date_utils.py
───────────────────
Trading calendar utilities for NSE (National Stock Exchange, India).

All functions operate on date / datetime objects and return date objects
unless otherwise stated.  No pandas dependency — pure stdlib + pytz.

NSE trading hours:  09:15 – 15:30 IST (Asia/Kolkata, UTC+5:30)
Weekly holidays:    Saturday, Sunday
Annual holidays:    Loaded from NSE_HOLIDAYS constant below.

Usage:
    from utils.date_utils import (
        is_trading_day, prev_trading_day, next_trading_day,
        trading_days_between, market_is_open, ist_now,
    )
"""

from __future__ import annotations

import functools
from datetime import date, datetime, time, timedelta
from typing import Iterator

import pytz

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)

# NSE declared holidays (approximate — updated for 2024 & 2025).
# Source: NSE India official holiday calendar.
# Add future years here as they are announced.
NSE_HOLIDAYS: frozenset[date] = frozenset({
    # ── 2024 ──────────────────────────────────────────────────────────────
    date(2024, 1, 22),   # Ram Mandir Consecration (special closure)
    date(2024, 1, 26),   # Republic Day
    date(2024, 3, 25),   # Holi
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 11),   # Id-Ul-Fitr (Eid)
    date(2024, 4, 14),   # Dr. Ambedkar Jayanti / Ram Navami
    date(2024, 4, 17),   # Ram Navami
    date(2024, 4, 21),   # Mahavir Jayanti
    date(2024, 5, 23),   # Buddha Purnima
    date(2024, 6, 17),   # Bakri Id
    date(2024, 7, 17),   # Muharram
    date(2024, 8, 15),   # Independence Day
    date(2024, 10, 2),   # Gandhi Jayanti
    date(2024, 10, 24),  # Diwali Laxmi Puja (Muhurat trading — treat as holiday)
    date(2024, 11, 1),   # Diwali Balipratipada
    date(2024, 11, 15),  # Gurunanak Jayanti
    date(2024, 12, 25),  # Christmas
    # ── 2025 ──────────────────────────────────────────────────────────────
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Eid)
    date(2025, 4, 10),   # Shri Ram Navami
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 12),   # Buddha Purnima
    date(2025, 6, 7),    # Bakri Id
    date(2025, 6, 27),   # Muharram
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 2),   # Gandhi Jayanti (also Dussehra in some years)
    date(2025, 10, 20),  # Diwali Laxmi Puja
    date(2025, 10, 21),  # Diwali Balipratipada
    date(2025, 11, 5),   # Gurunanak Jayanti
    date(2025, 12, 25),  # Christmas
    # ── 2026 ──────────────────────────────────────────────────────────────
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 19),   # Holi
    date(2026, 3, 20),   # Gudi Padwa
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 4, 20),   # Ram Navami / Mahavir Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 11, 14),  # Gurunanak Jayanti (Diwali approximate)
    date(2026, 12, 25),  # Christmas
})


# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_trading_day(d: date) -> bool:
    """
    Return True if *d* is an NSE trading day.
    Excludes weekends and all dates in NSE_HOLIDAYS.
    """
    if d.weekday() >= 5:       # 5 = Saturday, 6 = Sunday
        return False
    return d not in NSE_HOLIDAYS


def prev_trading_day(d: date, n: int = 1) -> date:
    """
    Return the *n*-th trading day strictly before *d*.

    Examples:
        prev_trading_day(date(2024, 1, 15))           → 2024-01-12 (Friday)
        prev_trading_day(date(2024, 1, 15), n=3)      → 3 trading days back
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    current = d - timedelta(days=1)
    found = 0
    while True:
        if is_trading_day(current):
            found += 1
            if found == n:
                return current
        current -= timedelta(days=1)


def next_trading_day(d: date, n: int = 1) -> date:
    """
    Return the *n*-th trading day strictly after *d*.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    current = d + timedelta(days=1)
    found = 0
    while True:
        if is_trading_day(current):
            found += 1
            if found == n:
                return current
        current += timedelta(days=1)


def trading_days_between(start: date, end: date, inclusive: bool = True) -> list[date]:
    """
    Return a list of all NSE trading days in [start, end] (inclusive by default).

    Args:
        start:      Start date.
        end:        End date (must be >= start).
        inclusive:  If True, include both start and end if they are trading days.

    Raises:
        ValueError: If end < start.
    """
    if end < start:
        raise ValueError(f"end ({end}) must be >= start ({start})")

    days: list[date] = []
    current = start
    while current <= end:
        if is_trading_day(current):
            if inclusive or (current != start and current != end):
                days.append(current)
        current += timedelta(days=1)
    return days


def count_trading_days(start: date, end: date) -> int:
    """
    Return the number of NSE trading days in [start, end] inclusive.
    Faster than len(trading_days_between(...)) for large ranges.
    """
    return sum(1 for _ in _iter_trading_days(start, end))


def _iter_trading_days(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        if is_trading_day(current):
            yield current
        current += timedelta(days=1)


def trading_days_ago(n: int, reference: date | None = None) -> date:
    """
    Return the date that is *n* trading days before *reference*.
    If reference is None, uses today's date in IST.

    Useful for computing lookback windows:
        sma200_start = trading_days_ago(200)
    """
    ref = reference or ist_now().date()
    return prev_trading_day(ref, n=n)


# ─────────────────────────────────────────────────────────────────────────────
# Market-hours helpers
# ─────────────────────────────────────────────────────────────────────────────

def ist_now() -> datetime:
    """Return the current datetime in IST (Asia/Kolkata)."""
    return datetime.now(tz=IST)


def market_is_open(at: datetime | None = None) -> bool:
    """
    Return True if NSE market is currently open.

    Args:
        at: Timezone-aware datetime to check.  Defaults to now (IST).

    Returns:
        True if *at* falls on a trading day between MARKET_OPEN and
        MARKET_CLOSE (09:15 – 15:30 IST, inclusive).
    """
    check = (at or ist_now()).astimezone(IST)
    if not is_trading_day(check.date()):
        return False
    return MARKET_OPEN <= check.time() <= MARKET_CLOSE


def minutes_to_market_open(at: datetime | None = None) -> int | None:
    """
    Return the number of minutes until the next market open from *at*.
    Returns None if market is currently open.
    Returns 0 if market opens at exactly *at*.
    """
    check = (at or ist_now()).astimezone(IST)
    if market_is_open(check):
        return None

    # Find next trading day
    candidate = check.date()
    if not is_trading_day(candidate) or check.time() > MARKET_CLOSE:
        candidate = next_trading_day(candidate)

    open_dt = IST.localize(datetime.combine(candidate, MARKET_OPEN))
    delta = open_dt - check
    return max(0, int(delta.total_seconds() / 60))


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing / formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_date(value: str | date | datetime) -> date:
    """
    Coerce a string, date, or datetime to a date object.

    Accepted string formats:
        "YYYY-MM-DD", "DD-MM-YYYY", "DD/MM/YYYY"

    Raises:
        ValueError: If the string cannot be parsed.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        value = value.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(
            f"Cannot parse date string '{value}'. "
            "Expected formats: YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY."
        )
    raise TypeError(f"Expected str, date, or datetime, got {type(value).__name__}")


def today_ist() -> date:
    """Return today's date in IST."""
    return ist_now().date()


def format_date(d: date, fmt: str = "%Y-%m-%d") -> str:
    """Format a date as a string (default ISO-8601)."""
    return d.strftime(fmt)


# ─────────────────────────────────────────────────────────────────────────────
# Data-range helpers used by ingestion
# ─────────────────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=128)
def last_n_trading_days(n: int, reference: date | None = None) -> tuple[date, ...]:
    """
    Return the last *n* trading days ending on (and including) *reference*
    (or today if None).  Result is cached per (n, reference) pair.
    Returned as a tuple so it is hashable / cacheable.
    """
    ref = reference or today_ist()
    days: list[date] = []
    current = ref
    while len(days) < n:
        if is_trading_day(current):
            days.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(days))


def required_history_start(
    n_trading_days: int,
    reference: date | None = None,
    buffer_pct: float = 0.40,
) -> date:
    """
    Return a calendar start date that *should* provide at least
    *n_trading_days* of trading data, adding a calendar buffer for
    weekends and holidays.

    Example:
        required_history_start(200)
        → A date roughly 280 calendar days before today
          (200 trading days × ~1.40 calendar-day ratio)

    Args:
        n_trading_days: Number of trading days needed.
        reference:      End date (default: today IST).
        buffer_pct:     Extra calendar days as a fraction of n_trading_days.
                        0.40 = 40% buffer, handles weekends + ~10 holidays.
    """
    ref = reference or today_ist()
    calendar_days = int(n_trading_days * (1.0 + buffer_pct))
    return ref - timedelta(days=calendar_days)
