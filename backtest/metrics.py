"""
backtest/metrics.py
───────────────────
Pure-computation metrics engine for the Minervini AI backtesting system.

Responsibilities
────────────────
  • Build a daily equity curve from a list of closed trade dicts.
  • Compute CAGR, Sharpe ratio, max drawdown, win rate, profit factor,
    expectancy, avg R-multiple, and per-regime breakdowns.
  • Assemble all figures into a single BacktestMetrics dataclass.

Design rules
────────────
  • Zero I/O, zero side effects — pure functions only.
  • pandas is used only inside compute_equity_curve(); all scalar math
    uses plain Python floats/lists to avoid unnecessary overhead.
  • All edge cases (empty trades, zero capital, single trade) return
    safe defaults rather than raising.
  • Fail loudly via BacktestError when inputs are fundamentally invalid
    (e.g. negative initial_capital).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from utils.exceptions import BacktestError
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestMetrics:
    """
    Aggregated performance statistics for one backtest run.

    All percentage fields are expressed as plain percentages
    (e.g. 18.4 means 18.4 %, not 0.184).
    max_drawdown_pct is always ≤ 0.
    profit_factor is float('inf') when there are zero losing trades.
    by_regime is populated by regime.py; defaults to empty dict here.
    """

    # ── Trade counts ──────────────────────────────────────────────────────
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0           # winning_trades / total_trades * 100

    # ── Returns ───────────────────────────────────────────────────────────
    cagr: float = 0.0               # Compound Annual Growth Rate %
    total_return_pct: float = 0.0   # (final_equity / initial_capital - 1) * 100
    sharpe_ratio: float = 0.0       # annualised Sharpe (daily returns, rf=0)
    max_drawdown_pct: float = 0.0   # peak-to-trough % drawdown (≤ 0)

    # ── Per-trade stats ───────────────────────────────────────────────────
    avg_r_multiple: float = 0.0     # avg (pnl / initial_risk) across trades
    profit_factor: float = 0.0      # gross_wins / abs(gross_losses)
    expectancy_pct: float = 0.0     # (wr * avg_win) - (lr * avg_loss)

    # ── Regime breakdown (populated later by regime.py) ───────────────────
    by_regime: dict[str, dict] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Equity curve
# ─────────────────────────────────────────────────────────────────────────────

def compute_equity_curve(
    trades: list[dict],
    initial_capital: float,
) -> pd.DataFrame:
    """
    Build a daily equity curve from a list of closed trade dicts.

    Each entry in *trades* must contain:
        exit_date  (date | str)  — the day the trade closed
        pnl        (float)       — realised profit / loss in currency units

    Days with no trade close carry equity forward; their daily_return_pct is 0.
    Trades closing on the same day have their P&L summed.

    Returns
    ───────
    pd.DataFrame with:
        DatetimeIndex  — one row per calendar day from the earliest exit to the latest
        equity         — running account value
        daily_return_pct — day-over-day % change in equity (0 on carry-forward days)

    Raises
    ──────
    BacktestError  — if initial_capital ≤ 0
    """
    if initial_capital <= 0:
        raise BacktestError(
            "initial_capital must be positive",
            initial_capital=initial_capital,
        )

    if not trades:
        log.debug("compute_equity_curve called with empty trades list")
        return pd.DataFrame(columns=["equity", "daily_return_pct"])

    # ── Aggregate P&L by exit date ────────────────────────────────────────
    pnl_by_date: dict[date, float] = {}
    for t in trades:
        exit_dt = t["exit_date"]
        if isinstance(exit_dt, str):
            exit_dt = date.fromisoformat(exit_dt)
        pnl_by_date[exit_dt] = pnl_by_date.get(exit_dt, 0.0) + float(t["pnl"])

    # ── Build daily index from first to last exit ─────────────────────────
    start = min(pnl_by_date)
    end = max(pnl_by_date)
    day_count = (end - start).days + 1

    dates: list[date] = [start + timedelta(days=i) for i in range(day_count)]
    equity_values: list[float] = []
    return_values: list[float] = []

    running_equity = initial_capital
    for d in dates:
        prev_equity = running_equity
        daily_pnl = pnl_by_date.get(d, 0.0)
        running_equity += daily_pnl
        daily_ret = ((running_equity - prev_equity) / prev_equity * 100.0
                     if prev_equity != 0 else 0.0)
        equity_values.append(running_equity)
        return_values.append(daily_ret)

    df = pd.DataFrame(
        {"equity": equity_values, "daily_return_pct": return_values},
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in dates]),
    )
    log.debug(
        "Equity curve built",
        rows=len(df),
        start=str(start),
        end=str(end),
        final_equity=round(running_equity, 2),
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Scalar metrics
# ─────────────────────────────────────────────────────────────────────────────

def compute_cagr(
    equity_curve: pd.DataFrame,
    initial_capital: float,
) -> float:
    """
    CAGR = (final_equity / initial_capital) ^ (365 / calendar_days) - 1

    Returns percentage (e.g. 18.4 means 18.4 %).
    Returns 0.0 if fewer than 2 data points or initial_capital is 0.
    """
    if equity_curve.empty or len(equity_curve) < 2 or initial_capital == 0:
        return 0.0

    final_equity: float = float(equity_curve["equity"].iloc[-1])
    calendar_days: int = (equity_curve.index[-1] - equity_curve.index[0]).days

    if calendar_days <= 0:
        return 0.0

    ratio = final_equity / initial_capital
    if ratio <= 0:
        return 0.0

    cagr_decimal = ratio ** (365.0 / calendar_days) - 1.0
    return round(cagr_decimal * 100.0, 4)


def compute_sharpe(
    equity_curve: pd.DataFrame,
    risk_free_rate: float = 0.0,
) -> float:
    """
    Annualised Sharpe ratio from the daily_return_pct column.

    sharpe = mean(daily_returns - rf_daily) / std(daily_returns) * sqrt(252)

    risk_free_rate is the **annual** rate expressed as a percentage (e.g. 5.0).
    Returns 0.0 if std is zero or fewer than 2 data points.
    """
    if equity_curve.empty or len(equity_curve) < 2:
        return 0.0

    daily_rf = risk_free_rate / 252.0
    excess = equity_curve["daily_return_pct"] - daily_rf
    std = float(excess.std(ddof=1))

    if std == 0.0 or math.isnan(std):
        return 0.0

    sharpe = float(excess.mean()) / std * math.sqrt(252)
    return round(sharpe, 4)


def compute_max_drawdown(equity_curve: pd.DataFrame) -> float:
    """
    Max peak-to-trough drawdown on the equity column.

    Returns a **negative** percentage (e.g. -23.5 means 23.5 % drawdown).
    Returns 0.0 if equity_curve is empty.
    """
    if equity_curve.empty:
        return 0.0

    eq = equity_curve["equity"]
    rolling_peak = eq.cummax()
    drawdown_series = (eq - rolling_peak) / rolling_peak * 100.0
    max_dd = float(drawdown_series.min())
    return round(max_dd, 4)


def compute_profit_factor(trades: list[dict]) -> float:
    """
    gross_wins / abs(gross_losses).

    Returns float('inf') if there are no losing trades.
    Returns 0.0 if there are no winning trades.
    """
    gross_wins = sum(t["pnl"] for t in trades if float(t["pnl"]) > 0)
    gross_losses = sum(t["pnl"] for t in trades if float(t["pnl"]) < 0)

    if gross_losses == 0.0:
        return float("inf") if gross_wins > 0 else 0.0

    return round(abs(gross_wins / gross_losses), 4)


# ─────────────────────────────────────────────────────────────────────────────
# Master assembly function
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(
    trades: list[dict],
    initial_capital: float,
    by_regime: dict[str, dict] | None = None,
) -> BacktestMetrics:
    """
    Master function — builds the full BacktestMetrics from a list of
    closed trade dicts produced by backtest/engine.py.

    Required keys per trade dict
    ────────────────────────────
        entry_date     (date | str)
        exit_date      (date | str)
        entry_price    (float)
        exit_price     (float)
        qty            (int)
        pnl            (float)      — realised P&L in currency
        pnl_pct        (float)      — realised P&L as % of entry cost
        initial_risk   (float)      — (entry_price - stop_loss) * qty
        setup_quality  (str)        — e.g. "A+", "B", "FAIL"
        regime         (str | None) — e.g. "Bull", "Bear", "Sideways"

    Raises
    ──────
    BacktestError  — if initial_capital ≤ 0
    """
    if initial_capital <= 0:
        raise BacktestError(
            "initial_capital must be positive",
            initial_capital=initial_capital,
        )

    log.info("compute_metrics called", num_trades=len(trades))

    # ── Empty trades guard ────────────────────────────────────────────────
    if not trades:
        log.warning("No trades provided — returning default BacktestMetrics")
        return BacktestMetrics(by_regime=by_regime or {})

    # ── Trade counts ──────────────────────────────────────────────────────
    total = len(trades)
    winners = [t for t in trades if float(t["pnl"]) > 0]
    losers  = [t for t in trades if float(t["pnl"]) < 0]
    n_win = len(winners)
    n_lose = len(losers)
    win_rate = n_win / total * 100.0

    # ── Per-trade stats ───────────────────────────────────────────────────
    # R-multiple: pnl / initial_risk (skip trades where initial_risk == 0)
    r_multiples = [
        float(t["pnl"]) / float(t["initial_risk"])
        for t in trades
        if float(t.get("initial_risk", 0)) != 0
    ]
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

    # Expectancy: (win_rate * avg_win_pct) - (loss_rate * |avg_loss_pct|)
    avg_win_pct = (
        sum(float(t["pnl_pct"]) for t in winners) / n_win if n_win else 0.0
    )
    avg_loss_pct = (
        sum(float(t["pnl_pct"]) for t in losers) / n_lose if n_lose else 0.0
    )
    loss_rate = 1.0 - (win_rate / 100.0)
    expectancy = (win_rate / 100.0) * avg_win_pct - loss_rate * abs(avg_loss_pct)

    # ── Equity curve, CAGR, Sharpe, drawdown ─────────────────────────────
    equity_curve = compute_equity_curve(trades, initial_capital)
    cagr = compute_cagr(equity_curve, initial_capital)
    sharpe = compute_sharpe(equity_curve)
    max_dd = compute_max_drawdown(equity_curve)

    # Total return
    final_equity = (
        float(equity_curve["equity"].iloc[-1])
        if not equity_curve.empty
        else initial_capital
    )
    total_return = (final_equity / initial_capital - 1.0) * 100.0

    # Profit factor
    pf = compute_profit_factor(trades)

    metrics = BacktestMetrics(
        total_trades=total,
        winning_trades=n_win,
        losing_trades=n_lose,
        win_rate=round(win_rate, 2),
        cagr=cagr,
        total_return_pct=round(total_return, 4),
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        avg_r_multiple=round(avg_r, 4),
        profit_factor=pf,
        expectancy_pct=round(expectancy, 4),
        by_regime=by_regime or {},
    )

    log.info(
        "Metrics assembled",
        total_trades=total,
        win_rate=metrics.win_rate,
        cagr=metrics.cagr,
        sharpe=metrics.sharpe_ratio,
        max_dd=metrics.max_drawdown_pct,
    )
    return metrics
