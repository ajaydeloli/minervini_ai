"""
tests/unit/test_backtest_metrics.py
────────────────────────────────────
Unit tests for backtest/metrics.py.

Coverage
────────
  test_empty_trades_returns_defaults           — all-zero BacktestMetrics
  test_single_winning_trade_win_rate           — win_rate == 100
  test_single_winning_trade_profit_factor_inf  — profit_factor == inf
  test_single_losing_trade_win_rate            — win_rate == 0
  test_single_losing_trade_profit_factor_zero  — profit_factor == 0
  test_fifty_pct_win_rate_expectancy           — correct expectancy formula
  test_max_drawdown_known_curve                — peak-to-trough on synthetic curve
  test_max_drawdown_empty_curve                — returns 0.0
  test_sharpe_known_returns                    — spot-check annualised Sharpe
  test_sharpe_zero_std                         — returns 0.0
  test_cagr_one_year_doubling                  — 100% CAGR
  test_cagr_fewer_than_two_points              — returns 0.0
  test_profit_factor_mixed                     — ratio of gross wins / losses
  test_equity_curve_gap_days                   — gaps carry equity forward
  test_equity_curve_empty_trades               — returns empty DataFrame
  test_compute_metrics_negative_capital        — raises BacktestError
  test_avg_r_multiple                          — correct averaging
  test_total_return_pct                        — simple end-to-end check
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from backtest.metrics import (
    BacktestMetrics,
    compute_cagr,
    compute_equity_curve,
    compute_max_drawdown,
    compute_metrics,
    compute_profit_factor,
    compute_sharpe,
)
from utils.exceptions import BacktestError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _trade(
    pnl: float,
    pnl_pct: float,
    exit_date: date | str = date(2024, 6, 1),
    initial_risk: float = 1_000.0,
    regime: str | None = "Bull",
) -> dict:
    """Minimal closed-trade dict accepted by compute_metrics."""
    if isinstance(exit_date, str):
        exit_date = date.fromisoformat(exit_date)
    return {
        "entry_date": date(2024, 5, 1),
        "exit_date": exit_date,
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl / 10,
        "qty": 10,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "initial_risk": initial_risk,
        "setup_quality": "B",
        "regime": regime,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BacktestMetrics defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_trades_returns_defaults():
    m = compute_metrics([], initial_capital=100_000.0)
    assert isinstance(m, BacktestMetrics)
    assert m.total_trades == 0
    assert m.winning_trades == 0
    assert m.losing_trades == 0
    assert m.win_rate == 0.0
    assert m.cagr == 0.0
    assert m.total_return_pct == 0.0
    assert m.sharpe_ratio == 0.0
    assert m.max_drawdown_pct == 0.0
    assert m.avg_r_multiple == 0.0
    assert m.profit_factor == 0.0
    assert m.expectancy_pct == 0.0
    assert m.by_regime == {}


# ─────────────────────────────────────────────────────────────────────────────
# Single winning trade
# ─────────────────────────────────────────────────────────────────────────────

def test_single_winning_trade_win_rate():
    m = compute_metrics([_trade(pnl=5_000.0, pnl_pct=5.0)], initial_capital=100_000.0)
    assert m.total_trades == 1
    assert m.winning_trades == 1
    assert m.losing_trades == 0
    assert m.win_rate == 100.0


def test_single_winning_trade_profit_factor_inf():
    m = compute_metrics([_trade(pnl=5_000.0, pnl_pct=5.0)], initial_capital=100_000.0)
    assert math.isinf(m.profit_factor)


def test_single_winning_trade_expectancy_positive():
    m = compute_metrics([_trade(pnl=5_000.0, pnl_pct=5.0)], initial_capital=100_000.0)
    assert m.expectancy_pct > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Single losing trade
# ─────────────────────────────────────────────────────────────────────────────

def test_single_losing_trade_win_rate():
    m = compute_metrics([_trade(pnl=-3_000.0, pnl_pct=-3.0)], initial_capital=100_000.0)
    assert m.win_rate == 0.0
    assert m.winning_trades == 0
    assert m.losing_trades == 1


def test_single_losing_trade_profit_factor_zero():
    m = compute_metrics([_trade(pnl=-3_000.0, pnl_pct=-3.0)], initial_capital=100_000.0)
    assert m.profit_factor == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 50 % win rate — expectancy check
# ─────────────────────────────────────────────────────────────────────────────

def test_fifty_pct_win_rate_expectancy():
    """
    1 win (+10 % pnl_pct) and 1 loss (-5 % pnl_pct).
    expectancy = (0.5 * 10) - (0.5 * 5) = 2.5
    """
    trades = [
        _trade(pnl=10_000.0, pnl_pct=10.0, exit_date=date(2024, 6, 1)),
        _trade(pnl=-5_000.0, pnl_pct=-5.0, exit_date=date(2024, 6, 15)),
    ]
    m = compute_metrics(trades, initial_capital=100_000.0)
    assert m.win_rate == 50.0
    assert abs(m.expectancy_pct - 2.5) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# compute_max_drawdown
# ─────────────────────────────────────────────────────────────────────────────

def test_max_drawdown_known_curve():
    """
    Equity: 100_000 → 120_000 → 90_000 → 110_000
    Peak at 120_000; trough at 90_000 → drawdown = (90-120)/120 * 100 = -25 %
    """
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02"),
         pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")]
    )
    df = pd.DataFrame(
        {"equity": [100_000.0, 120_000.0, 90_000.0, 110_000.0],
         "daily_return_pct": [0.0, 20.0, -25.0, 22.2]},
        index=idx,
    )
    dd = compute_max_drawdown(df)
    assert abs(dd - (-25.0)) < 1e-3


def test_max_drawdown_empty_curve():
    df = pd.DataFrame(columns=["equity", "daily_return_pct"])
    assert compute_max_drawdown(df) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# compute_sharpe
# ─────────────────────────────────────────────────────────────────────────────

def test_sharpe_known_returns():
    """
    Constant daily return of 0.1 % → std=0 on a uniform series, so we use
    a two-element series where std is well-defined.
    daily returns: [1.0, -1.0]  mean=0, std=sqrt(2), sharpe=0.
    """
    idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")])
    df = pd.DataFrame(
        {"equity": [100_000.0, 101_000.0],
         "daily_return_pct": [1.0, -1.0]},
        index=idx,
    )
    sharpe = compute_sharpe(df)
    assert sharpe == 0.0


def test_sharpe_positive_returns():
    """
    All returns = +0.5 % → std=0 → sharpe returns 0.0 (guards zero std).
    But with two different positive values std > 0.
    """
    idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")])
    df = pd.DataFrame(
        {"equity": [100_000.0, 101_000.0],
         "daily_return_pct": [1.0, 2.0]},
        index=idx,
    )
    sharpe = compute_sharpe(df)
    # mean=1.5, std=0.7071, sharpe = 1.5/0.7071 * sqrt(252) ≈ 33.66
    assert sharpe > 0.0


def test_sharpe_zero_std():
    idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")])
    df = pd.DataFrame(
        {"equity": [100_000.0, 101_000.0],
         "daily_return_pct": [0.5, 0.5]},
        index=idx,
    )
    assert compute_sharpe(df) == 0.0


def test_sharpe_fewer_than_two_points():
    idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01")])
    df = pd.DataFrame({"equity": [100_000.0], "daily_return_pct": [0.5]}, index=idx)
    assert compute_sharpe(df) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# compute_cagr
# ─────────────────────────────────────────────────────────────────────────────

def test_cagr_one_year_doubling():
    """
    Equity doubles over exactly 365 days → CAGR ≈ 100 %.
    """
    start = pd.Timestamp("2024-01-01")
    end   = pd.Timestamp("2024-12-31")  # 365-day span
    idx = pd.DatetimeIndex([start, end])
    df = pd.DataFrame(
        {"equity": [100_000.0, 200_000.0],
         "daily_return_pct": [0.0, 100.0]},
        index=idx,
    )
    cagr = compute_cagr(df, initial_capital=100_000.0)
    assert abs(cagr - 100.0) < 0.5  # within half a percent of 100 %


def test_cagr_fewer_than_two_points():
    idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01")])
    df = pd.DataFrame(
        {"equity": [110_000.0], "daily_return_pct": [10.0]},
        index=idx,
    )
    assert compute_cagr(df, initial_capital=100_000.0) == 0.0


def test_cagr_same_day_start_end():
    """Same start and end date → calendar_days == 0 → returns 0.0."""
    ts = pd.Timestamp("2024-06-01")
    idx = pd.DatetimeIndex([ts, ts])
    df = pd.DataFrame(
        {"equity": [100_000.0, 110_000.0], "daily_return_pct": [0.0, 10.0]},
        index=idx,
    )
    assert compute_cagr(df, initial_capital=100_000.0) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# compute_profit_factor
# ─────────────────────────────────────────────────────────────────────────────

def test_profit_factor_mixed():
    """
    gross_wins = 6_000 + 4_000 = 10_000
    gross_losses = 2_000 + 3_000 = 5_000
    profit_factor = 10_000 / 5_000 = 2.0
    """
    trades = [
        _trade(pnl=6_000.0,  pnl_pct=6.0,  exit_date=date(2024, 6, 1)),
        _trade(pnl=4_000.0,  pnl_pct=4.0,  exit_date=date(2024, 6, 5)),
        _trade(pnl=-2_000.0, pnl_pct=-2.0, exit_date=date(2024, 6, 10)),
        _trade(pnl=-3_000.0, pnl_pct=-3.0, exit_date=date(2024, 6, 15)),
    ]
    pf = compute_profit_factor(trades)
    assert abs(pf - 2.0) < 1e-6


def test_profit_factor_no_wins_returns_zero():
    trades = [_trade(pnl=-1_000.0, pnl_pct=-1.0)]
    assert compute_profit_factor(trades) == 0.0


def test_profit_factor_no_losses_returns_inf():
    trades = [_trade(pnl=5_000.0, pnl_pct=5.0)]
    assert math.isinf(compute_profit_factor(trades))


# ─────────────────────────────────────────────────────────────────────────────
# compute_equity_curve — gap handling
# ─────────────────────────────────────────────────────────────────────────────

def test_equity_curve_gap_days():
    """
    Two trades: close on Jan 1 and Jan 10.
    The curve must have 10 rows (Jan 1 – Jan 10 inclusive).
    Days 2–9 are gap days: daily_return_pct == 0, equity carried forward.
    Final equity = 100_000 + 1_000 + 2_000 = 103_000.
    """
    trades = [
        _trade(pnl=1_000.0, pnl_pct=1.0, exit_date=date(2024, 1, 1)),
        _trade(pnl=2_000.0, pnl_pct=2.0, exit_date=date(2024, 1, 10)),
    ]
    df = compute_equity_curve(trades, initial_capital=100_000.0)
    assert len(df) == 10
    # Gap rows (index 1 through 8) have zero return
    assert (df["daily_return_pct"].iloc[1:9] == 0.0).all()
    # Equity carries forward unchanged through gap
    assert abs(df["equity"].iloc[8] - 101_000.0) < 1e-6
    # Final equity after second trade
    assert abs(df["equity"].iloc[-1] - 103_000.0) < 1e-6


def test_equity_curve_same_day_trades_pnl_summed():
    """Two trades closing on the same day: their P&L must be summed."""
    trades = [
        _trade(pnl=1_000.0, pnl_pct=1.0, exit_date=date(2024, 3, 15)),
        _trade(pnl=2_000.0, pnl_pct=2.0, exit_date=date(2024, 3, 15)),
    ]
    df = compute_equity_curve(trades, initial_capital=100_000.0)
    assert len(df) == 1
    assert abs(df["equity"].iloc[0] - 103_000.0) < 1e-6


def test_equity_curve_empty_trades():
    df = compute_equity_curve([], initial_capital=100_000.0)
    assert df.empty


# ─────────────────────────────────────────────────────────────────────────────
# BacktestError guards
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_metrics_negative_capital_raises():
    with pytest.raises(BacktestError):
        compute_metrics([], initial_capital=-1.0)


def test_compute_metrics_zero_capital_raises():
    with pytest.raises(BacktestError):
        compute_metrics([], initial_capital=0.0)


def test_compute_equity_curve_zero_capital_raises():
    with pytest.raises(BacktestError):
        compute_equity_curve(
            [_trade(pnl=100.0, pnl_pct=1.0)],
            initial_capital=0.0,
        )


# ─────────────────────────────────────────────────────────────────────────────
# avg_r_multiple
# ─────────────────────────────────────────────────────────────────────────────

def test_avg_r_multiple():
    """
    Trade 1: pnl=3_000, risk=1_000 → R = +3
    Trade 2: pnl=-1_000, risk=1_000 → R = -1
    avg_r = (3 + -1) / 2 = 1.0
    """
    trades = [
        _trade(pnl=3_000.0,  pnl_pct=3.0,  initial_risk=1_000.0,
               exit_date=date(2024, 6, 1)),
        _trade(pnl=-1_000.0, pnl_pct=-1.0, initial_risk=1_000.0,
               exit_date=date(2024, 6, 15)),
    ]
    m = compute_metrics(trades, initial_capital=100_000.0)
    assert abs(m.avg_r_multiple - 1.0) < 1e-6


def test_avg_r_multiple_zero_risk_skipped():
    """Trades with initial_risk == 0 must be excluded from the average."""
    trades = [
        _trade(pnl=5_000.0, pnl_pct=5.0, initial_risk=0.0,
               exit_date=date(2024, 6, 1)),
        _trade(pnl=2_000.0, pnl_pct=2.0, initial_risk=1_000.0,
               exit_date=date(2024, 6, 10)),
    ]
    m = compute_metrics(trades, initial_capital=100_000.0)
    # Only second trade counts: R = 2_000 / 1_000 = 2.0
    assert abs(m.avg_r_multiple - 2.0) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# total_return_pct
# ─────────────────────────────────────────────────────────────────────────────

def test_total_return_pct():
    """Single +10_000 pnl on 100_000 capital → total_return = 10.0 %."""
    trades = [_trade(pnl=10_000.0, pnl_pct=10.0)]
    m = compute_metrics(trades, initial_capital=100_000.0)
    assert abs(m.total_return_pct - 10.0) < 1e-6


def test_total_return_pct_negative():
    """Net loss scenario: final equity below initial → total_return < 0."""
    trades = [_trade(pnl=-20_000.0, pnl_pct=-20.0)]
    m = compute_metrics(trades, initial_capital=100_000.0)
    assert abs(m.total_return_pct - (-20.0)) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# by_regime pass-through
# ─────────────────────────────────────────────────────────────────────────────

def test_by_regime_passed_through():
    regime_data = {"Bull": {"trades": 5, "win_rate": 80.0, "avg_pnl_pct": 4.2}}
    m = compute_metrics(
        [_trade(pnl=1_000.0, pnl_pct=1.0)],
        initial_capital=100_000.0,
        by_regime=regime_data,
    )
    assert m.by_regime == regime_data
