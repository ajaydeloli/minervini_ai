"""
paper_trading/portfolio.py
──────────────────────────
SQLite-backed paper-trading portfolio for the Minervini AI system.

Tables
──────
    paper_positions         — one row per paper trade (open or closed)
    paper_portfolio_state   — singleton row tracking cash and summary stats

Public API
──────────
    init_paper_trading_tables(db_path)
    get_portfolio_state(db_path) -> PortfolioState
    get_open_positions(db_path)  -> list[Trade]
    get_position(db_path, symbol) -> Trade | None
    open_position(db_path, trade) -> Trade
    close_position(db_path, trade_id, exit_date, exit_price, exit_reason) -> Trade
    get_closed_trades(db_path)   -> list[Trade]
    reset_portfolio(db_path, initial_capital)

Design notes
────────────
    • Plain sqlite3 — no pandas, no ORM.
    • All writes run inside a transaction (context-manager commit/rollback).
    • init_paper_trading_tables() is idempotent (CREATE TABLE IF NOT EXISTS).
    • initial_capital is sourced from config/settings.yaml; defaults to
      100 000 if the file is absent or the key is missing.
    • Fail-loud: raises PaperTradingError rather than silently continuing.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Generator, Literal, Optional

import yaml

from utils.exceptions import PaperTradingError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config helper
# ─────────────────────────────────────────────────────────────────────────────

_SETTINGS_PATH = Path(__file__).parents[1] / "config" / "settings.yaml"


def _load_initial_capital() -> float:
    """Read paper_trading.initial_capital from settings.yaml (default 100 000)."""
    try:
        with open(_SETTINGS_PATH) as fh:
            cfg = yaml.safe_load(fh)
        return float(cfg["paper_trading"]["initial_capital"])
    except Exception:
        return 100_000.0


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS paper_positions (
    id            INTEGER PRIMARY KEY,
    symbol        TEXT    NOT NULL,
    entry_date    DATE    NOT NULL,
    entry_price   REAL    NOT NULL,
    qty           INTEGER NOT NULL,
    stop_loss     REAL    NOT NULL,
    target_price  REAL,
    risk_pct      REAL,
    rr_ratio      REAL,
    setup_quality TEXT,
    score         INTEGER,
    pyramided     INTEGER DEFAULT 0,
    status        TEXT    DEFAULT 'open',
    exit_date     DATE,
    exit_price    REAL,
    exit_reason   TEXT,
    pnl           REAL,
    pnl_pct       REAL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_portfolio_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cash            REAL    NOT NULL,
    initial_capital REAL    NOT NULL,
    total_trades    INTEGER DEFAULT 0,
    win_trades      INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Connection helper
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a WAL-mode sqlite3 connection that commits on clean exit
    or rolls back on exception, then closes.
    """
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
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    """Represents one paper trade (open or closed)."""
    symbol:        str
    entry_date:    date
    entry_price:   float
    qty:           int
    stop_loss:     float
    # optional fields — may be None before or after the trade is closed
    id:            Optional[int]   = None
    target_price:  Optional[float] = None
    risk_pct:      Optional[float] = None
    rr_ratio:      Optional[float] = None
    setup_quality: Optional[str]   = None
    score:         Optional[int]   = None
    pyramided:     bool            = False
    status:        str             = "open"
    exit_date:     Optional[date]  = None
    exit_price:    Optional[float] = None
    exit_reason:   Optional[str]   = None   # 'stop_loss' | 'target' | 'manual'
    pnl:           Optional[float] = None
    pnl_pct:       Optional[float] = None


@dataclass
class PortfolioState:
    """Singleton state row for the paper portfolio."""
    cash:            float
    initial_capital: float
    total_trades:    int = 0
    win_trades:      int = 0

    @property
    def win_rate(self) -> float:
        """Fraction of winning trades (0.0 when no trades yet)."""
        if self.total_trades == 0:
            return 0.0
        return self.win_trades / self.total_trades

    def total_return_pct(self, current_open_value: float) -> float:
        """
        Overall return relative to initial_capital.

        Args:
            current_open_value: Sum of (entry_price * qty) for all open positions
                                at current market prices — caller must supply this.
        Returns:
            Percentage gain/loss vs initial_capital (e.g. 12.5 means +12.5 %).
        """
        if self.initial_capital == 0:
            return 0.0
        portfolio_value = self.cash + current_open_value
        return (portfolio_value / self.initial_capital - 1.0) * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Internal row → dataclass helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_trade(row: sqlite3.Row) -> Trade:
    d = dict(row)
    return Trade(
        id=d["id"],
        symbol=d["symbol"],
        entry_date=_parse_date(d["entry_date"]),
        entry_price=d["entry_price"],
        qty=d["qty"],
        stop_loss=d["stop_loss"],
        target_price=d.get("target_price"),
        risk_pct=d.get("risk_pct"),
        rr_ratio=d.get("rr_ratio"),
        setup_quality=d.get("setup_quality"),
        score=d.get("score"),
        pyramided=bool(d.get("pyramided", 0)),
        status=d.get("status", "open"),
        exit_date=_parse_date(d["exit_date"]) if d.get("exit_date") else None,
        exit_price=d.get("exit_price"),
        exit_reason=d.get("exit_reason"),
        pnl=d.get("pnl"),
        pnl_pct=d.get("pnl_pct"),
    )


def _parse_date(val) -> date:
    """Accept a date object or ISO string and return a date."""
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    raise PaperTradingError(f"Cannot parse date value: {val!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Schema initialisation
# ─────────────────────────────────────────────────────────────────────────────

def init_paper_trading_tables(db_path: Path) -> None:
    """
    Create paper_positions and paper_portfolio_state tables if they don't exist,
    and seed the singleton portfolio_state row using initial_capital from config.

    Idempotent — safe to call at every application startup.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initial_capital = _load_initial_capital()

    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        # Seed the singleton row only if it doesn't exist yet
        conn.execute(
            """
            INSERT INTO paper_portfolio_state (id, cash, initial_capital)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (initial_capital, initial_capital),
        )

    log.info(
        "Paper trading tables initialised",
        db=str(db_path),
        initial_capital=initial_capital,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Read functions
# ─────────────────────────────────────────────────────────────────────────────

def get_portfolio_state(db_path: Path) -> PortfolioState:
    """
    Return the singleton PortfolioState row.

    Raises:
        PaperTradingError: If the portfolio has not been initialised
                           (init_paper_trading_tables not yet called).
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM paper_portfolio_state WHERE id = 1"
        ).fetchone()

    if row is None:
        raise PaperTradingError(
            "Paper trading portfolio is not initialised. "
            "Call init_paper_trading_tables() first."
        )

    d = dict(row)
    return PortfolioState(
        cash=d["cash"],
        initial_capital=d["initial_capital"],
        total_trades=d.get("total_trades", 0),
        win_trades=d.get("win_trades", 0),
    )


def get_open_positions(db_path: Path) -> list[Trade]:
    """Return all open paper positions."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status = 'open'"
        ).fetchall()
    return [_row_to_trade(r) for r in rows]


def get_position(db_path: Path, symbol: str) -> Optional[Trade]:
    """
    Return the open position for *symbol*, or None if not held.

    Only returns the first open row (a symbol should not have two open
    positions simultaneously in a single-entry paper portfolio).
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM paper_positions WHERE symbol = ? AND status = 'open' LIMIT 1",
            (symbol.upper().strip(),),
        ).fetchone()
    return _row_to_trade(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Write functions
# ─────────────────────────────────────────────────────────────────────────────

def open_position(db_path: Path, trade: Trade) -> Trade:
    """
    INSERT a new paper position and deduct cost from cash.

    Raises:
        PaperTradingError: If cash is insufficient for the trade cost.

    Returns:
        The same Trade with its database id populated.
    """
    cost = trade.entry_price * trade.qty

    with _connect(db_path) as conn:
        # --- cash check inside the same transaction ---
        row = conn.execute(
            "SELECT cash FROM paper_portfolio_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise PaperTradingError("Portfolio not initialised.")
        cash = row["cash"]
        if cost > cash:
            raise PaperTradingError(
                f"Insufficient cash to open {trade.symbol}: "
                f"need ₹{cost:,.2f}, have ₹{cash:,.2f}",
                symbol=trade.symbol,
            )

        cursor = conn.execute(
            """
            INSERT INTO paper_positions
                (symbol, entry_date, entry_price, qty, stop_loss,
                 target_price, risk_pct, rr_ratio, setup_quality, score,
                 pyramided, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                trade.symbol.upper().strip(),
                str(trade.entry_date),
                trade.entry_price,
                trade.qty,
                trade.stop_loss,
                trade.target_price,
                trade.risk_pct,
                trade.rr_ratio,
                trade.setup_quality,
                trade.score,
                int(trade.pyramided),
            ),
        )
        trade_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE paper_portfolio_state
               SET cash         = cash - ?,
                   total_trades = total_trades + 1,
                   updated_at   = ?
             WHERE id = 1
            """,
            (cost, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")),
        )

    log.info(
        "Paper position opened",
        symbol=trade.symbol,
        qty=trade.qty,
        entry_price=trade.entry_price,
        cost=cost,
    )
    trade.id = trade_id
    return trade


def close_position(
    db_path: Path,
    trade_id: int,
    exit_date: date | str,
    exit_price: float,
    exit_reason: Literal["stop_loss", "target", "manual"],
) -> Trade:
    """
    Mark a position as closed, compute PnL, and credit cash.

    Raises:
        PaperTradingError: If trade_id is not found or position is not open.

    Returns:
        Updated Trade with exit fields and pnl populated.
    """
    exit_date_str = str(exit_date)[:10]

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM paper_positions WHERE id = ? AND status = 'open'",
            (trade_id,),
        ).fetchone()
        if row is None:
            raise PaperTradingError(
                f"No open position found with id={trade_id}.",
                trade_id=trade_id,
            )

        d = dict(row)
        qty = d["qty"]
        entry_price = d["entry_price"]

        pnl = (exit_price - entry_price) * qty
        pnl_pct = (exit_price / entry_price - 1.0) * 100.0
        proceeds = exit_price * qty
        is_win = 1 if pnl > 0 else 0

        conn.execute(
            """
            UPDATE paper_positions
               SET status      = 'closed',
                   exit_date   = ?,
                   exit_price  = ?,
                   exit_reason = ?,
                   pnl         = ?,
                   pnl_pct     = ?
             WHERE id = ?
            """,
            (exit_date_str, exit_price, exit_reason, pnl, pnl_pct, trade_id),
        )
        conn.execute(
            """
            UPDATE paper_portfolio_state
               SET cash       = cash + ?,
                   win_trades = win_trades + ?,
                   updated_at = ?
             WHERE id = 1
            """,
            (proceeds, is_win, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")),
        )

        # Re-fetch to return the fully-updated row
        updated_row = conn.execute(
            "SELECT * FROM paper_positions WHERE id = ?", (trade_id,)
        ).fetchone()

    log.info(
        "Paper position closed",
        trade_id=trade_id,
        symbol=d["symbol"],
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        reason=exit_reason,
    )
    return _row_to_trade(updated_row)


def get_closed_trades(db_path: Path) -> list[Trade]:
    """Return all closed trades, most recently closed first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM paper_positions
             WHERE status = 'closed'
             ORDER BY exit_date DESC, id DESC
            """
        ).fetchall()
    return [_row_to_trade(r) for r in rows]


def mark_pyramided(db_path: Path, trade_id: int) -> None:
    """
    Set pyramided = 1 on an existing open position row.

    Called by simulator.pyramid_position() after a pyramid add-on is opened
    so that the original position cannot be pyramided a second time.

    Raises:
        PaperTradingError: If trade_id is not found.
    """
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE paper_positions SET pyramided = 1 WHERE id = ? AND status = 'open'",
            (trade_id,),
        )
        if cursor.rowcount == 0:
            raise PaperTradingError(
                f"mark_pyramided: no open position with id={trade_id}",
                trade_id=trade_id,
            )
    log.debug("Position marked pyramided", trade_id=trade_id)


def reset_portfolio(db_path: Path, initial_capital: float) -> None:
    """
    Hard-reset the paper portfolio.

    Deletes ALL positions (open and closed) and resets the portfolio
    state singleton to *initial_capital* with zero counters.

    Args:
        db_path:         Path to the SQLite database.
        initial_capital: New starting capital (₹).
    """
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM paper_positions")
        conn.execute(
            """
            UPDATE paper_portfolio_state
               SET cash            = ?,
                   initial_capital = ?,
                   total_trades    = 0,
                   win_trades      = 0,
                   updated_at      = ?
             WHERE id = 1
            """,
            (
                initial_capital,
                initial_capital,
                datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )

    log.warning(
        "Paper portfolio reset",
        initial_capital=initial_capital,
    )
