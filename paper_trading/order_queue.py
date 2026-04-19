"""
paper_trading/order_queue.py
─────────────────────────────
SQLite-backed pending-order queue for the Minervini AI paper-trading simulator.

Signals from the daily 15:35 IST screen arrive outside market hours.
Orders are queued here and filled at the next trading day's open price.
The queue survives server restarts because it is persisted in SQLite.

Public API
──────────
    init_order_queue_table(db_path)
    is_market_open(dt=None) -> bool
    next_market_open() -> datetime
    queue_order(db_path, symbol, order_type, entry_price, stop_loss,
                target_price, risk_pct, rr_ratio, setup_quality,
                score, expiry_days=2) -> PendingOrder
    get_pending_orders(db_path) -> list[PendingOrder]
    execute_pending_orders(db_path, current_prices, config) -> list[Trade]
    cancel_expired_orders(db_path) -> int
    cancel_order(db_path, symbol) -> bool

Design notes
────────────
    • zoneinfo.ZoneInfo("Asia/Kolkata") — stdlib, Python 3.9+.
    • All datetimes stored as UTC strings in SQLite; IST used for logic only.
    • is_market_open / next_market_open: weekday-only check (no NSE holidays).
    • INSERT OR REPLACE ensures one pending order per symbol at most.
    • execute_pending_orders wraps each fill in its own try/except.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Generator, Optional
from zoneinfo import ZoneInfo

from utils.exceptions import PaperTradingError
from utils.logger import get_logger
from utils.date_utils import next_trading_day
from paper_trading.portfolio import Trade, open_position

log = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN  = time(9, 15)
_MARKET_CLOSE = time(15, 30)

# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_pending_orders (
    id            INTEGER PRIMARY KEY,
    symbol        TEXT    NOT NULL UNIQUE,
    order_type    TEXT    NOT NULL,
    entry_price   REAL    NOT NULL,
    stop_loss     REAL    NOT NULL,
    target_price  REAL,
    risk_pct      REAL,
    rr_ratio      REAL,
    setup_quality TEXT,
    score         INTEGER,
    queued_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at    DATE    NOT NULL
);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Connection helper (mirrors portfolio.py pattern)
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PendingOrder:
    """One queued order waiting to be filled at next market open."""
    symbol:        str
    order_type:    str          # 'enter' | 'pyramid' | 'exit'
    entry_price:   float        # signal price at queue time
    stop_loss:     float
    expires_at:    date
    id:            Optional[int]   = None
    target_price:  Optional[float] = None
    risk_pct:      Optional[float] = None
    rr_ratio:      Optional[float] = None
    setup_quality: Optional[str]   = None
    score:         Optional[int]   = None
    queued_at:     Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Row → dataclass helper
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_pending(row: sqlite3.Row) -> PendingOrder:
    d = dict(row)
    queued_raw = d.get("queued_at")
    queued_dt: Optional[datetime] = None
    if queued_raw:
        try:
            queued_dt = datetime.fromisoformat(str(queued_raw))
        except ValueError:
            queued_dt = None
    expires_raw = d["expires_at"]
    if isinstance(expires_raw, str):
        expires = date.fromisoformat(expires_raw[:10])
    else:
        expires = expires_raw
    return PendingOrder(
        id=d["id"],
        symbol=d["symbol"],
        order_type=d["order_type"],
        entry_price=d["entry_price"],
        stop_loss=d["stop_loss"],
        target_price=d.get("target_price"),
        risk_pct=d.get("risk_pct"),
        rr_ratio=d.get("rr_ratio"),
        setup_quality=d.get("setup_quality"),
        score=d.get("score"),
        queued_at=queued_dt,
        expires_at=expires,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Schema init
# ─────────────────────────────────────────────────────────────────────────────

def init_order_queue_table(db_path: Path) -> None:
    """
    Create paper_pending_orders table if it does not exist.
    Idempotent — safe to call at every startup.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(_QUEUE_SCHEMA)
    log.info("Order queue table initialised", db=str(db_path))


# ─────────────────────────────────────────────────────────────────────────────
# Market-hours helpers (pure — accept dt param for testing)
# ─────────────────────────────────────────────────────────────────────────────

def is_market_open(dt: datetime | None = None) -> bool:
    """
    Return True if *dt* (default: now) is a weekday within 09:15–15:30 IST.
    Weekday check only — does not account for NSE holidays.

    Args:
        dt: Any timezone-aware or naive datetime.  Naive is treated as UTC.
            If None, uses datetime.now(IST).
    """
    if dt is None:
        check = datetime.now(tz=IST)
    else:
        check = dt.astimezone(IST) if dt.tzinfo else dt.replace(tzinfo=IST)
    if check.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    return _MARKET_OPEN <= check.time() <= _MARKET_CLOSE


def next_market_open(dt: datetime | None = None) -> datetime:
    """
    Return the next 09:15:00 IST open datetime from *dt* (default: now).

    Rules (weekday-only, no holiday awareness):
      - Mon–Fri, before 09:15 IST  →  today at 09:15 IST
      - Mon–Fri, 09:15–15:30 IST   →  (market is currently open)
                                       return next trading day at 09:15
      - Mon–Fri, after 09:15 IST   →  next weekday at 09:15
      - Saturday                   →  Monday at 09:15
      - Sunday                     →  Monday at 09:15
    """
    if dt is None:
        now = datetime.now(tz=IST)
    else:
        now = dt.astimezone(IST) if dt.tzinfo else dt.replace(tzinfo=IST)

    today = now.date()
    weekday = today.weekday()   # 0=Mon … 6=Sun
    current_time = now.time()

    # Determine candidate date
    if weekday < 5 and current_time < _MARKET_OPEN:
        # Today, before open
        candidate = today
    else:
        # After open (or weekend) — advance to next weekday
        candidate = today + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)

    return datetime(
        candidate.year, candidate.month, candidate.day,
        _MARKET_OPEN.hour, _MARKET_OPEN.minute, 0,
        tzinfo=IST,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Queue operations
# ─────────────────────────────────────────────────────────────────────────────

def queue_order(
    db_path: Path,
    symbol: str,
    order_type: str,
    entry_price: float,
    stop_loss: float,
    target_price: Optional[float],
    risk_pct: Optional[float],
    rr_ratio: Optional[float],
    setup_quality: str,
    score: int,
    expiry_days: int = 2,
) -> PendingOrder:
    """
    Upsert a pending order for *symbol* (INSERT OR REPLACE).

    expires_at is set to today + *expiry_days* trading days (using
    next_trading_day from date_utils so it skips weekends/holidays).

    Returns:
        The freshly inserted/replaced PendingOrder.
    """
    symbol = symbol.upper().strip()
    today = datetime.now(tz=IST).date()

    # Compute expiry: step forward expiry_days trading days
    expires = today
    for _ in range(expiry_days):
        expires = next_trading_day(expires)

    queued_utc = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO paper_pending_orders
                (symbol, order_type, entry_price, stop_loss, target_price,
                 risk_pct, rr_ratio, setup_quality, score, queued_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol, order_type, entry_price, stop_loss, target_price,
                risk_pct, rr_ratio, setup_quality, score, queued_utc,
                str(expires),
            ),
        )
        row = conn.execute(
            "SELECT * FROM paper_pending_orders WHERE symbol = ?", (symbol,)
        ).fetchone()

    order = _row_to_pending(row)
    log.info(
        "Order queued",
        symbol=symbol,
        order_type=order_type,
        entry_price=entry_price,
        expires_at=str(expires),
    )
    return order


def get_pending_orders(db_path: Path) -> list[PendingOrder]:
    """Return all rows from paper_pending_orders."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM paper_pending_orders ORDER BY queued_at"
        ).fetchall()
    return [_row_to_pending(r) for r in rows]


def execute_pending_orders(
    db_path: Path,
    current_prices: dict[str, float],
    config: dict,
) -> list[Trade]:
    """
    Fill all pending orders that have a current price available.

    Called at market open (or on demand).  For each pending order:
      - Missing price  → log warning, skip.
      - Expired        → delete row, log warning, skip.
      - Otherwise      → fill at current_prices[symbol], call open_position(),
                         delete pending row, append Trade to result list.

    Each fill is wrapped in its own try/except; one failure does not
    abort the remaining orders.

    Args:
        db_path:        Path to the SQLite database.
        current_prices: Mapping of symbol → fill price (open price of the day).
        config:         Full settings dict (passed through to open_position if needed).

    Returns:
        List of Trade objects that were successfully filled.
    """
    pending = get_pending_orders(db_path)
    today = datetime.now(tz=IST).date()
    filled: list[Trade] = []

    for order in pending:
        try:
            # ── price check ──────────────────────────────────────────────────
            if order.symbol not in current_prices:
                log.warning(
                    "No current price for pending order — skipping",
                    symbol=order.symbol,
                )
                continue

            # ── expiry check ─────────────────────────────────────────────────
            if today > order.expires_at:
                log.warning(
                    "Pending order expired — cancelling",
                    symbol=order.symbol,
                    expires_at=str(order.expires_at),
                )
                _delete_pending(db_path, order.symbol)
                continue

            fill_price = current_prices[order.symbol]

            # ── size from config ──────────────────────────────────────────────
            pt_cfg = config.get("paper_trading", {})
            initial_capital = float(pt_cfg.get("initial_capital", 100_000))
            risk_pct_cfg    = float(pt_cfg.get("risk_per_trade_pct", 2.0))

            risk_amount = initial_capital * risk_pct_cfg / 100.0
            risk_per_share = abs(fill_price - order.stop_loss)
            if risk_per_share <= 0:
                log.warning(
                    "Stop-loss equals fill price — skipping order",
                    symbol=order.symbol,
                    fill_price=fill_price,
                    stop_loss=order.stop_loss,
                )
                continue
            qty = max(1, int(risk_amount / risk_per_share))

            trade = Trade(
                symbol=order.symbol,
                entry_date=today,
                entry_price=fill_price,
                qty=qty,
                stop_loss=order.stop_loss,
                target_price=order.target_price,
                risk_pct=order.risk_pct,
                rr_ratio=order.rr_ratio,
                setup_quality=order.setup_quality,
                score=order.score,
            )

            filled_trade = open_position(db_path, trade)
            _delete_pending(db_path, order.symbol)
            filled.append(filled_trade)

            log.info(
                "Pending order filled",
                symbol=order.symbol,
                fill_price=fill_price,
                qty=qty,
            )

        except Exception as exc:  # noqa: BLE001
            log.error(
                "Failed to fill pending order",
                symbol=order.symbol,
                error=str(exc),
            )

    return filled


# ─────────────────────────────────────────────────────────────────────────────
# Cancel helpers
# ─────────────────────────────────────────────────────────────────────────────

def _delete_pending(db_path: Path, symbol: str) -> None:
    """Internal: delete a single pending order row by symbol."""
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM paper_pending_orders WHERE symbol = ?",
            (symbol.upper().strip(),),
        )


def cancel_expired_orders(db_path: Path) -> int:
    """
    Delete all pending orders whose expires_at < today (IST).

    Returns:
        Number of rows deleted.
    """
    today = datetime.now(tz=IST).date()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM paper_pending_orders WHERE expires_at < ?",
            (str(today),),
        )
        count = cursor.rowcount

    if count:
        log.info("Expired pending orders cancelled", count=count, today=str(today))
    return count


def fetch_fill_prices(symbols: list[str], config: dict) -> dict[str, float]:
    """
    Fetch the best available fill prices for a list of NSE symbols.

    Called by the market-open scheduler job and by runner.py Step 0 when
    the pipeline is invoked while the market is open.

    Strategy (per symbol):
      1. Download the last 7 calendar days via YFinanceSource.
      2. If the most-recent row is from today and open > 0  →  use today's open.
      3. Otherwise                                          →  use the previous
         session's close price (best proxy for next-open fill in paper trading).

    Symbols that fail to fetch are logged as warnings and omitted from the
    returned dict — execute_pending_orders() already handles missing symbols
    gracefully.

    Args:
        symbols: List of NSE symbols whose pending orders need a fill price.
        config:  Full application config dict (used to locate universe_yaml).

    Returns:
        dict[symbol → fill price]  (may be empty if all fetches fail).
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    prices: dict[str, float] = {}
    if not symbols:
        return prices

    try:
        from ingestion.yfinance_source import YFinanceSource
    except ImportError as exc:
        log.error(
            "fetch_fill_prices: cannot import YFinanceSource",
            error=str(exc),
        )
        return prices

    universe_yaml = config.get("universe_yaml_path", "config/universe.yaml")
    try:
        src = YFinanceSource(universe_yaml=universe_yaml)
    except Exception as exc:
        log.warning("fetch_fill_prices: YFinanceSource init failed", error=str(exc))
        return prices

    _ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(tz=_ist).date()
    lookback_start = today - timedelta(days=7)

    for symbol in symbols:
        try:
            df = src.fetch(symbol, start=lookback_start, end=today)
            if df.empty:
                log.warning("fetch_fill_prices: empty data", symbol=symbol)
                continue

            latest_row  = df.iloc[-1]
            latest_date = df.index[-1]
            if hasattr(latest_date, "date"):
                latest_date = latest_date.date()

            open_price  = float(latest_row.get("open",  0))
            close_price = float(latest_row.get("close", 0))

            if latest_date == today and open_price > 0:
                prices[symbol] = open_price
                log.debug(
                    "fetch_fill_prices: today open",
                    symbol=symbol,
                    price=open_price,
                )
            elif close_price > 0:
                prices[symbol] = close_price
                log.debug(
                    "fetch_fill_prices: prev-session close",
                    symbol=symbol,
                    price=close_price,
                    data_date=str(latest_date),
                )
            else:
                log.warning(
                    "fetch_fill_prices: both open and close are zero — skipping",
                    symbol=symbol,
                )
        except Exception as exc:
            log.warning(
                "fetch_fill_prices: fetch failed",
                symbol=symbol,
                error=str(exc),
            )

    log.info(
        "fetch_fill_prices: complete",
        requested=len(symbols),
        fetched=len(prices),
    )
    return prices


def cancel_order(db_path: Path, symbol: str) -> bool:
    """
    Delete the pending order for *symbol*.

    Returns:
        True if a row was deleted, False if no order existed for that symbol.
    """
    symbol = symbol.upper().strip()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM paper_pending_orders WHERE symbol = ?",
            (symbol,),
        )
        deleted = cursor.rowcount > 0

    if deleted:
        log.info("Pending order cancelled", symbol=symbol)
    else:
        log.debug("cancel_order: no pending order found", symbol=symbol)
    return deleted
