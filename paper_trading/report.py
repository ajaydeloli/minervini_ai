"""
paper_trading/report.py
───────────────────────
Phase 7 — Read-only reporting module for the Minervini AI paper portfolio.

Provides:
    PortfolioSummary        — dataclass of all computed portfolio metrics
    get_portfolio_summary() — loads DB state and builds PortfolioSummary
    format_summary_text()   — human-readable multi-line string for logs/Telegram
    get_performance_by_quality() — win-rate / avg-PnL grouped by setup_quality

Design mandates
───────────────
    • ZERO DB writes — every function is strictly read-only.
    • No pandas — plain Python arithmetic on dataclass lists.
    • Handles an empty portfolio gracefully (no open / closed trades).
    • current_prices=None → uses entry_price as mark (unrealised_pnl = 0).
    • Monetary display uses Indian comma formatting via f"{v:,.0f}".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from paper_trading.portfolio import (
    PortfolioState,
    Trade,
    get_closed_trades,
    get_open_positions,
    get_portfolio_state,
)
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PortfolioSummary dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PortfolioSummary:
    """All computed portfolio metrics — no DB fields, purely derived."""

    # Cash & valuation
    cash:             float
    open_value:       float          # sum(qty × current_price) for open positions
    total_value:      float          # cash + open_value
    initial_capital:  float

    # Returns
    total_return:     float          # total_value - initial_capital
    total_return_pct: float          # (total_return / initial_capital) × 100

    # P&L split
    realised_pnl:     float          # sum of pnl for closed trades
    unrealised_pnl:   float          # sum of (current_price - entry_price) × qty for open

    # Trade counts
    total_trades:     int
    open_trades:      int
    closed_trades:    int
    win_trades:       int
    win_rate:         float          # win_trades / closed_trades × 100 (0 if none)

    # Quality metrics
    avg_rr_realised:  float          # avg rr_ratio of winning closed trades (0 if none)

    # Detail lists
    positions:        list[dict] = field(default_factory=list)   # open positions
    recent_closed:    list[dict] = field(default_factory=list)   # last 10 closed


# ─────────────────────────────────────────────────────────────────────────────
# get_portfolio_summary
# ─────────────────────────────────────────────────────────────────────────────

def get_portfolio_summary(
    db_path: Path,
    current_prices: Optional[dict[str, float]] = None,
) -> PortfolioSummary:
    """
    Build a PortfolioSummary by reading DB state (read-only).

    Parameters
    ──────────
    db_path        : Path to the SQLite database.
    current_prices : Mapping of symbol → current market price.
                     If None (or symbol absent), entry_price is used as mark
                     so unrealised_pnl = 0 for that position.

    Returns
    ───────
    PortfolioSummary with all fields computed.
    """
    db_path = Path(db_path)
    prices: dict[str, float] = current_prices or {}

    # ── Load raw data from DB ────────────────────────────────────────────────
    state: PortfolioState = get_portfolio_state(db_path)
    open_pos: list[Trade] = get_open_positions(db_path)
    closed_trades: list[Trade] = get_closed_trades(db_path)

    today = date.today()

    # ── Open positions ────────────────────────────────────────────────────────
    open_value    = 0.0
    unrealised_pnl = 0.0
    positions_list: list[dict] = []

    for t in open_pos:
        mark = prices.get(t.symbol, t.entry_price)
        pos_open_value = mark * t.qty
        pos_unrealised = (mark - t.entry_price) * t.qty
        pos_unrealised_pct = (
            (mark / t.entry_price - 1.0) * 100.0 if t.entry_price else 0.0
        )
        days_held = (today - t.entry_date).days if t.entry_date else 0

        open_value     += pos_open_value
        unrealised_pnl += pos_unrealised

        positions_list.append({
            "symbol":           t.symbol,
            "entry_date":       t.entry_date,
            "entry_price":      t.entry_price,
            "qty":              t.qty,
            "stop_loss":        t.stop_loss,
            "target_price":     t.target_price,
            "current_price":    mark,
            "unrealised_pnl":   round(pos_unrealised, 2),
            "unrealised_pnl_pct": round(pos_unrealised_pct, 2),
            "setup_quality":    t.setup_quality,
            "score":            t.score,
            "pyramided":        t.pyramided,
            "rr_ratio":         t.rr_ratio,
            "days_held":        days_held,
        })

    # ── Closed trades ─────────────────────────────────────────────────────────
    realised_pnl = 0.0
    win_trades   = 0
    rr_wins: list[float] = []
    recent_closed: list[dict] = []

    for t in closed_trades:
        pnl = t.pnl or 0.0
        realised_pnl += pnl
        if pnl > 0:
            win_trades += 1
            if t.rr_ratio is not None:
                rr_wins.append(t.rr_ratio)

    # Build detail list for last 10 (get_closed_trades returns newest first)
    for t in closed_trades[:10]:
        days_held = (
            (t.exit_date - t.entry_date).days
            if t.exit_date and t.entry_date
            else 0
        )
        recent_closed.append({
            "symbol":        t.symbol,
            "entry_date":    t.entry_date,
            "exit_date":     t.exit_date,
            "entry_price":   t.entry_price,
            "exit_price":    t.exit_price,
            "qty":           t.qty,
            "pnl":           round(t.pnl or 0.0, 2),
            "pnl_pct":       round(t.pnl_pct or 0.0, 2),
            "exit_reason":   t.exit_reason,
            "setup_quality": t.setup_quality,
            "rr_ratio":      t.rr_ratio,
            "days_held":     days_held,
        })

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    total_value      = state.cash + open_value
    total_return     = total_value - state.initial_capital
    total_return_pct = (
        (total_return / state.initial_capital) * 100.0
        if state.initial_capital
        else 0.0
    )

    closed_count  = len(closed_trades)
    open_count    = len(open_pos)
    total_trades  = state.total_trades   # authoritative counter from DB

    win_rate = (win_trades / closed_count * 100.0) if closed_count else 0.0
    avg_rr_realised = (sum(rr_wins) / len(rr_wins)) if rr_wins else 0.0

    return PortfolioSummary(
        cash=state.cash,
        open_value=round(open_value, 2),
        total_value=round(total_value, 2),
        initial_capital=state.initial_capital,
        total_return=round(total_return, 2),
        total_return_pct=round(total_return_pct, 2),
        realised_pnl=round(realised_pnl, 2),
        unrealised_pnl=round(unrealised_pnl, 2),
        total_trades=total_trades,
        open_trades=open_count,
        closed_trades=closed_count,
        win_trades=win_trades,
        win_rate=round(win_rate, 1),
        avg_rr_realised=round(avg_rr_realised, 2),
        positions=positions_list,
        recent_closed=recent_closed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# format_summary_text
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(value: float) -> str:
    """Format a monetary value with Indian comma style (thousands separator)."""
    return f"{value:,.0f}"


def _signed(value: float, pct: Optional[float] = None) -> str:
    """Return a sign-prefixed monetary string, optionally with a % suffix."""
    sign = "+" if value >= 0 else "-"
    base = f"{sign}₹{_fmt(abs(value))}"
    if pct is not None:
        sign_pct = "+" if pct >= 0 else "-"
        base += f" ({sign_pct}{abs(pct):.2f}%)"
    return base


def format_summary_text(summary: PortfolioSummary) -> str:
    """
    Produce a human-readable multi-line portfolio summary.

    Suitable for pipeline log output and Telegram messages.
    """
    lines: list[str] = []
    lines.append("📊 Paper Portfolio Summary")
    lines.append("──────────────────────────")
    lines.append(
        f"Total Value   : ₹{_fmt(summary.total_value)}"
        f"  ({_signed(summary.total_return_pct)[-len(_signed(summary.total_return_pct)):]})"
    )

    # Simpler signed pct line
    sign = "+" if summary.total_return_pct >= 0 else ""
    lines[-1] = (
        f"Total Value   : ₹{_fmt(summary.total_value)}"
        f"  ({sign}{summary.total_return_pct:.2f}%)"
    )

    lines.append(f"Cash          : ₹{_fmt(summary.cash)}")
    lines.append(f"Open Value    : ₹{_fmt(summary.open_value)}")

    r_sign = "+" if summary.realised_pnl >= 0 else ""
    u_sign = "+" if summary.unrealised_pnl >= 0 else ""
    lines.append(f"Realised P&L  : {r_sign}₹{_fmt(abs(summary.realised_pnl))}"
                 if summary.realised_pnl >= 0
                 else f"Realised P&L  : -₹{_fmt(abs(summary.realised_pnl))}")
    lines.append(f"Unrealised P&L: {u_sign}₹{_fmt(abs(summary.unrealised_pnl))}"
                 if summary.unrealised_pnl >= 0
                 else f"Unrealised P&L: -₹{_fmt(abs(summary.unrealised_pnl))}")

    lines.append(
        f"Trades: {summary.total_trades} total | "
        f"{summary.closed_trades} closed | "
        f"{summary.open_trades} open | "
        f"Win rate: {summary.win_rate:.1f}%"
    )

    if summary.positions:
        lines.append("Open Positions:")
        for p in summary.positions:
            sym   = p["symbol"].ljust(10)
            qty   = p["qty"]
            ep    = p["entry_price"]
            sl    = p["stop_loss"]
            upnl  = p["unrealised_pnl"]
            upct  = p["unrealised_pnl_pct"]
            usign = "+" if upnl >= 0 else "-"
            pct_sign = "+" if upct >= 0 else ""
            lines.append(
                f"  {sym} {qty} qty @ ₹{_fmt(ep)}"
                f" | SL ₹{_fmt(sl)}"
                f" | Unrealised: {usign}₹{_fmt(abs(upnl))}"
                f" ({pct_sign}{upct:.1f}%)"
            )
    else:
        lines.append("Open Positions: none")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# get_performance_by_quality
# ─────────────────────────────────────────────────────────────────────────────

def get_performance_by_quality(db_path: Path) -> dict[str, dict]:
    """
    Return win rate and avg P&L grouped by setup_quality for closed trades.

    Structure
    ─────────
    {
        "A+": {"trades": 3, "wins": 2, "win_rate": 66.7, "avg_pnl_pct": 8.2},
        "A":  {"trades": 5, "wins": 3, "win_rate": 60.0, "avg_pnl_pct": 4.1},
        ...
    }

    Returns an empty dict when there are no closed trades.
    """
    db_path = Path(db_path)
    closed: list[Trade] = get_closed_trades(db_path)

    if not closed:
        return {}

    buckets: dict[str, dict] = {}

    for t in closed:
        quality = t.setup_quality or "UNKNOWN"
        if quality not in buckets:
            buckets[quality] = {"trades": 0, "wins": 0, "pnl_pct_sum": 0.0}

        buckets[quality]["trades"] += 1
        pnl = t.pnl or 0.0
        if pnl > 0:
            buckets[quality]["wins"] += 1
        buckets[quality]["pnl_pct_sum"] += t.pnl_pct or 0.0

    result: dict[str, dict] = {}
    for quality, data in buckets.items():
        trades = data["trades"]
        wins   = data["wins"]
        result[quality] = {
            "trades":      trades,
            "wins":        wins,
            "win_rate":    round(wins / trades * 100.0, 1) if trades else 0.0,
            "avg_pnl_pct": round(data["pnl_pct_sum"] / trades, 2) if trades else 0.0,
        }

    return result
