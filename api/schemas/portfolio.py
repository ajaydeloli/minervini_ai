"""
api/schemas/portfolio.py
────────────────────────
Pydantic v2 response models for paper trading endpoints.

Endpoints served
────────────────
  GET  /api/v1/portfolio         → PortfolioSummary (contains list[PositionRow])
  GET  /api/v1/portfolio/trades  → list[TradeRow]

Field names mirror:
  - PortfolioSummary dataclass → paper_trading/report.py
  - Trade dataclass            → paper_trading/portfolio.py
  - positions list dicts       → built in report.get_portfolio_summary()

Conventions (project-wide):
  - Python 3.11+ native generics / union syntax  (X | Y, list[T])
  - Pydantic v2 — no deprecated validators
  - All monetary fields are float (rupees); percentages are float
  - Nullable fields default to None — never use sentinel 0/-1
  - Dates are ISO strings ("YYYY-MM-DD"); datetimes are ISO strings
    with no timezone suffix — stored in IST by the paper trading engine
"""

from __future__ import annotations

from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# PositionRow — single open position within a portfolio summary
# ─────────────────────────────────────────────────────────────────────────────

class PositionRow(BaseModel):
    """
    One open position as returned inside PortfolioSummary.positions.

    Fields mirror the dicts built in
    paper_trading/report.py :: get_portfolio_summary() → positions_list.

    Fields
    ──────
    symbol              : NSE ticker.
    entry_price         : Price at which the position was opened (rupees).
    qty                 : Number of shares held.
    stop_loss           : Current stop-loss price (rupees).
    entry_date          : ISO date string "YYYY-MM-DD" of the entry trade.
    setup_quality       : SEPA quality tag at entry: A+, A, B, C, FAIL.
    current_price       : Latest mark price used for valuation; None when
                          no current-price feed is available (entry_price
                          is used as the mark in that case).
    unrealised_pnl      : (current_price − entry_price) × qty in rupees;
                          None when current_price is None.
    unrealised_pnl_pct  : Percentage P&L on cost basis; None when not available.
    pyramided           : True when additional shares were added after entry.
    """

    symbol:             str
    entry_price:        float
    qty:                int
    stop_loss:          float
    entry_date:         str           # ISO date "YYYY-MM-DD"
    setup_quality:      str           # A+, A, B, C, FAIL
    current_price:      float | None
    unrealised_pnl:     float | None
    unrealised_pnl_pct: float | None
    pyramided:          bool


# ─────────────────────────────────────────────────────────────────────────────
# PortfolioSummary — full paper portfolio state
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioSummary(BaseModel):
    """
    Full paper trading portfolio summary.

    Returned by GET /api/v1/portfolio.
    Mirrors the PortfolioSummary dataclass in paper_trading/report.py,
    except that nested position dicts are typed as list[PositionRow] here.

    Fields
    ──────
    cash              : Uninvested cash balance (rupees).
    open_value        : Market value of all open positions (rupees).
    total_value       : cash + open_value (rupees).
    initial_capital   : Starting capital configured at portfolio creation.
    total_return_pct  : (total_value − initial_capital) / initial_capital × 100.
    realised_pnl      : Cumulative P&L of all closed trades (rupees).
    unrealised_pnl    : Cumulative mark-to-market P&L of open positions (rupees).
    total_trades      : Total number of trades ever opened (authoritative DB counter).
    win_rate          : Percentage of closed trades with pnl > 0 (0.0 when none).
    open_positions    : Count of currently open positions.
    positions         : Detailed list of open positions; empty list when none.
    """

    cash:             float
    open_value:       float
    total_value:      float
    initial_capital:  float
    total_return_pct: float
    realised_pnl:     float
    unrealised_pnl:   float
    total_trades:     int
    win_rate:         float
    open_positions:   int
    positions:        list[PositionRow]


# ─────────────────────────────────────────────────────────────────────────────
# TradeRow — one row in GET /portfolio/trades
# ─────────────────────────────────────────────────────────────────────────────

class TradeRow(BaseModel):
    """
    One trade (open or closed) returned by GET /api/v1/portfolio/trades.

    Fields mirror the Trade dataclass in paper_trading/portfolio.py and the
    dict structure used in report.py :: recent_closed list.

    Fields
    ──────
    symbol        : NSE ticker.
    entry_price   : Price at which shares were bought (rupees).
    exit_price    : Price at which the position was closed; None when open.
    qty           : Number of shares in this trade leg.
    entry_date    : ISO date string "YYYY-MM-DD".
    exit_date     : ISO date string when closed; None when position is still open.
    status        : "open" when position is live; "closed" when fully exited.
    setup_quality : SEPA quality tag at entry: A+, A, B, C, FAIL.
    pnl           : Realised profit / loss in rupees; None when open.
    pnl_pct       : Realised P&L as a percentage of entry cost; None when open.
    """

    symbol:        str
    entry_price:   float
    exit_price:    float | None
    qty:           int
    entry_date:    str           # ISO date "YYYY-MM-DD"
    exit_date:     str   | None  # ISO date "YYYY-MM-DD" or None
    status:        str           # open | closed
    setup_quality: str           # A+, A, B, C, FAIL
    pnl:           float | None
    pnl_pct:       float | None
