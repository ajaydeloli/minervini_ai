"""
api/routers/portfolio.py
────────────────────────
Phase 10 — Read-only paper trading endpoints.

Endpoints
─────────
  GET /api/v1/portfolio
      Returns a full PortfolioSummary: cash, open value, total value,
      realised / unrealised P&L, win rate, and detailed open positions.
      Calls paper_trading.report.get_portfolio_summary() with an empty
      current_prices dict so unrealised P&L falls back to entry price
      (no live price feed available in the API layer).

  GET /api/v1/portfolio/trades
      Returns a flat list of TradeRow objects.
      Query param ?status=open|closed|all (default "all") controls which
      trades are fetched from the DB.

Design rules
────────────
  • READ-ONLY — no write functions are called, ever.
  • Both endpoints require X-API-Key authentication (require_read_key).
  • sqlite3 errors (e.g. tables missing because paper trading has never
    run) are caught and returned as err() — never raised.
  • get_db_path() from api/deps.py is the sole source of the DB path.
  • Monetary values are floats (rupees); dates are ISO strings.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from api.deps import get_db_path, require_read_key
from api.rate_limit import READ_LIMIT, limiter
from api.schemas.common import APIResponse, err, ok
from api.schemas.portfolio import PortfolioSummary, PositionRow, TradeRow
from paper_trading.portfolio import get_closed_trades, get_open_positions
from paper_trading.report import get_portfolio_summary
from utils.exceptions import PaperTradingError
from utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


# ─────────────────────────────────────────────────────────────────────────────
# Internal mapping helpers
# ─────────────────────────────────────────────────────────────────────────────

def _map_position_row(pos: dict) -> PositionRow:
    """
    Map one position dict (from report.get_portfolio_summary) to PositionRow.

    entry_date is a date object in the dict — we convert to ISO string here.
    current_price is the mark used by the report (entry_price when no live
    feed is available); we pass it through as-is.
    """
    entry_date = pos["entry_date"]
    entry_date_str = (
        entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date)
    )

    return PositionRow(
        symbol=pos["symbol"],
        entry_price=float(pos["entry_price"]),
        qty=int(pos["qty"]),
        stop_loss=float(pos["stop_loss"]),
        entry_date=entry_date_str,
        setup_quality=pos.get("setup_quality") or "",
        current_price=float(pos["current_price"]) if pos.get("current_price") is not None else None,
        unrealised_pnl=float(pos["unrealised_pnl"]) if pos.get("unrealised_pnl") is not None else None,
        unrealised_pnl_pct=float(pos["unrealised_pnl_pct"]) if pos.get("unrealised_pnl_pct") is not None else None,
        pyramided=bool(pos.get("pyramided", False)),
    )


def _map_trade_row(trade) -> TradeRow:
    """
    Map a paper_trading.portfolio.Trade dataclass to a TradeRow schema model.

    Dates are date objects in the dataclass — converted to ISO strings here.
    Nullable monetary fields (exit_price, pnl, pnl_pct) pass through as None
    for open trades.
    """
    entry_date_str = (
        trade.entry_date.isoformat()
        if hasattr(trade.entry_date, "isoformat")
        else str(trade.entry_date)
    )
    exit_date_str: str | None = None
    if trade.exit_date is not None:
        exit_date_str = (
            trade.exit_date.isoformat()
            if hasattr(trade.exit_date, "isoformat")
            else str(trade.exit_date)
        )

    return TradeRow(
        symbol=trade.symbol,
        entry_price=float(trade.entry_price),
        exit_price=float(trade.exit_price) if trade.exit_price is not None else None,
        qty=int(trade.qty),
        entry_date=entry_date_str,
        exit_date=exit_date_str,
        status=trade.status,
        setup_quality=trade.setup_quality or "",
        pnl=float(trade.pnl) if trade.pnl is not None else None,
        pnl_pct=float(trade.pnl_pct) if trade.pnl_pct is not None else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/portfolio
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=APIResponse[PortfolioSummary],
    summary="Paper portfolio summary",
    description=(
        "Returns cash, open value, total return, realised / unrealised P&L, "
        "win rate, and a detailed breakdown of all open positions. "
        "Unrealised P&L is computed using entry price as the mark (no live "
        "price feed is used by the API layer)."
    ),
)
@limiter.limit(READ_LIMIT)
def get_portfolio(
    request: Request,
    db_path: Path = Depends(get_db_path),
    _key: str = Depends(require_read_key),
) -> APIResponse[PortfolioSummary]:
    """
    GET /api/v1/portfolio

    Calls paper_trading.report.get_portfolio_summary() with current_prices={}
    so that unrealised P&L falls back to entry price for every open position
    (no live price feed).

    Returns err() — not an HTTP 500 — if the paper trading tables have not
    been created yet (i.e. the paper trading engine has never been run).
    """
    log.info("GET /portfolio requested", db=str(db_path))

    try:
        raw = get_portfolio_summary(db_path=db_path, current_prices={})
    except (sqlite3.Error, PaperTradingError) as exc:
        msg = (
            "Paper trading data is not available — "
            "the paper trading engine has not been run yet."
        )
        log.warning("Portfolio summary unavailable", reason=str(exc))
        return err(msg)
    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected error in GET /portfolio", exc_info=True)
        return err(f"Failed to load portfolio summary: {exc}")

    # Map positions list[dict] → list[PositionRow]
    try:
        positions = [_map_position_row(p) for p in raw.positions]
    except Exception as exc:  # noqa: BLE001
        log.error("Position mapping failed", exc_info=True)
        return err(f"Failed to serialise open positions: {exc}")

    summary = PortfolioSummary(
        cash=raw.cash,
        open_value=raw.open_value,
        total_value=raw.total_value,
        initial_capital=raw.initial_capital,
        total_return_pct=raw.total_return_pct,
        realised_pnl=raw.realised_pnl,
        unrealised_pnl=raw.unrealised_pnl,
        total_trades=raw.total_trades,
        win_rate=raw.win_rate,
        open_positions=raw.open_trades,
        positions=positions,
    )

    log.info(
        "Portfolio summary returned",
        total_value=raw.total_value,
        open_positions=raw.open_trades,
        total_trades=raw.total_trades,
    )
    return ok(summary)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/portfolio/trades
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/trades",
    response_model=APIResponse[list[TradeRow]],
    summary="Paper portfolio trade history",
    description=(
        "Returns a list of paper trades. "
        "Use ?status=open to see only open positions, ?status=closed for "
        "exited trades, or ?status=all (default) for both."
    ),
)
@limiter.limit(READ_LIMIT)
def get_trades(
    request: Request,
    status: Literal["open", "closed", "all"] = Query(
        default="all",
        description='Filter trades by status. One of "open", "closed", or "all".',
    ),
    db_path: Path = Depends(get_db_path),
    _key: str = Depends(require_read_key),
) -> APIResponse[list[TradeRow]]:
    """
    GET /api/v1/portfolio/trades

    Fetches open and/or closed trades from the paper_positions table
    depending on the ?status query parameter.

    Returns an empty list (not an error) when no trades exist yet.
    Returns err() — not an HTTP 500 — on any sqlite3 error (e.g. tables
    missing because paper trading has never been run).
    """
    log.info("GET /portfolio/trades requested", status=status, db=str(db_path))

    trades = []

    try:
        if status in ("open", "all"):
            open_trades = get_open_positions(db_path=db_path)
            trades.extend(open_trades)

        if status in ("closed", "all"):
            closed_trades = get_closed_trades(db_path=db_path)
            trades.extend(closed_trades)

    except (sqlite3.Error, PaperTradingError) as exc:
        msg = (
            "Paper trading data is not available — "
            "the paper trading engine has not been run yet."
        )
        log.warning("Trades query unavailable", status=status, reason=str(exc))
        return err(msg)
    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected error in GET /portfolio/trades", exc_info=True)
        return err(f"Failed to load trades: {exc}")

    # Map Trade dataclasses → TradeRow schema models
    try:
        rows = [_map_trade_row(t) for t in trades]
    except Exception as exc:  # noqa: BLE001
        log.error("Trade mapping failed", exc_info=True)
        return err(f"Failed to serialise trades: {exc}")

    log.info("Trades returned", status=status, count=len(rows))
    return ok(
        rows,
        meta={"total": len(rows), "status_filter": status},
    )
