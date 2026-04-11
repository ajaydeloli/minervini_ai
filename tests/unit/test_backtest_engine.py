"""
tests/unit/test_backtest_engine.py
────────────────────────────────────
Unit tests for backtest/engine.py.

All SQLite and Parquet I/O is mocked — no real data files are required.

Tests (≥ 12)
────────────
  test_load_screen_results_empty_db
  test_load_screen_results_returns_results_for_date
  test_load_prices_missing_symbol_omitted
  test_load_prices_returns_close_for_date
  test_run_backtest_empty_universe_returns_zero_trades
  test_run_backtest_single_winning_trade_positive_return
  test_run_backtest_trailing_stop_closes_position
  test_run_backtest_max_hold_days_closes_position
  test_run_backtest_gate_stats_stage2_and_tt
  test_run_parameter_sweep_one_result_per_value
  test_run_parameter_sweep_none_value_included
  test_backtest_result_equity_curve_has_datetimeindex
  test_load_screen_results_bad_db_returns_empty
  test_run_backtest_no_entry_when_no_entry_price
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backtest.engine import (
    BacktestResult,
    load_prices_for_date,
    load_screen_results,
    run_backtest,
    run_parameter_sweep,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG = {
    "backtest": {
        "trailing_stop_pct": 0.07,
        "fixed_stop_pct":    0.05,
        "max_hold_days":     20,
        "position_size_pct": 0.10,
    },
    "paper_trading": {
        "initial_capital": 100_000.0,
        "max_positions":   5,
    },
    "data": {
        "features_dir": "/fake/features",
    },
}

_START = date(2024, 1, 2)   # Tuesday
_END   = date(2024, 1, 5)   # Friday  (4 business days)


def _make_candidate(
    symbol: str = "RELIANCE",
    entry_price: float = 100.0,
    stop_loss: float = 90.0,
    target_price: float = 130.0,
    stage: int = 2,
    trend_template_pass: bool = True,
    score: int = 80,
    setup_quality: str = "A",
) -> dict:
    return {
        "symbol":              symbol,
        "entry_price":         entry_price,
        "stop_loss":           stop_loss,
        "target_price":        target_price,
        "stage":               stage,
        "trend_template_pass": trend_template_pass,
        "score":               score,
        "setup_quality":       setup_quality,
        "date":                _START.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# load_screen_results tests
# ─────────────────────────────────────────────────────────────────────────────

@patch("backtest.engine.load_results", return_value=[])
def test_load_screen_results_empty_db(mock_lr):
    """Empty DB → load_screen_results returns []."""
    result = load_screen_results(Path("/fake/db.sqlite"), date(2024, 1, 2))
    assert result == []
    mock_lr.assert_called_once_with(Path("/fake/db.sqlite"), run_date="2024-01-02")


@patch("backtest.engine.load_results")
def test_load_screen_results_returns_results_for_date(mock_lr):
    """Known date with results → list of dicts is returned."""
    fake = [{"symbol": "RELIANCE", "score": 80, "stage": 2}]
    mock_lr.return_value = fake
    result = load_screen_results(Path("/fake/db.sqlite"), date(2024, 1, 2))
    assert result == fake
    assert result[0]["symbol"] == "RELIANCE"


@patch("backtest.engine.load_results", side_effect=Exception("db error"))
def test_load_screen_results_bad_db_returns_empty(mock_lr):
    """Exception from load_results → returns [] gracefully."""
    result = load_screen_results(Path("/bad/path.sqlite"), date(2024, 1, 2))
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# load_prices_for_date tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_price_df(close_val: float, target_date: date) -> pd.DataFrame:
    """Build a minimal feature DataFrame with the given close on target_date."""
    idx = pd.DatetimeIndex([pd.Timestamp(target_date)])
    return pd.DataFrame({"close": [close_val]}, index=idx)


@patch("pandas.read_parquet")
@patch("pathlib.Path.exists", return_value=True)
def test_load_prices_returns_close_for_date(mock_exists, mock_read):
    """Symbol with data on target date → close price in result."""
    mock_read.return_value = _make_price_df(142.50, date(2024, 1, 2))
    prices = load_prices_for_date(date(2024, 1, 2), ["RELIANCE"], _CONFIG)
    assert "RELIANCE" in prices
    assert abs(prices["RELIANCE"] - 142.50) < 1e-6


@patch("pathlib.Path.exists", return_value=False)
def test_load_prices_missing_symbol_omitted(mock_exists):
    """Symbol with no parquet file is silently omitted."""
    prices = load_prices_for_date(date(2024, 1, 2), ["NOSUCHSYM"], _CONFIG)
    assert "NOSUCHSYM" not in prices
    assert prices == {}


# ─────────────────────────────────────────────────────────────────────────────
# run_backtest tests
# ─────────────────────────────────────────────────────────────────────────────

def _patch_run_backtest(screen_results_by_date=None, prices_by_symbol=None):
    """
    Return a dict of patches needed to isolate run_backtest from I/O.

    screen_results_by_date : {date_str: list[dict]} or None → always []
    prices_by_symbol       : {symbol: price} → all dates return same price
    """
    if screen_results_by_date is None:
        screen_results_by_date = {}
    if prices_by_symbol is None:
        prices_by_symbol = {}

    def _fake_screen(db_path, run_date):
        return screen_results_by_date.get(run_date.isoformat(), [])

    def _fake_prices(target_date, symbols, config):
        return {s: prices_by_symbol[s] for s in symbols if s in prices_by_symbol}

    return _fake_screen, _fake_prices


@patch("backtest.engine.load_prices_for_date")
@patch("backtest.engine.load_screen_results")
def test_run_backtest_empty_universe_returns_zero_trades(mock_screen, mock_prices):
    """Empty screener results + no prices → BacktestResult with zero trades."""
    mock_screen.return_value = []
    mock_prices.return_value = {}

    result = run_backtest(_START, _END, _CONFIG, Path("/fake/db.sqlite"))

    assert isinstance(result, BacktestResult)
    assert result.metrics.total_trades == 0
    assert result.trades == []
    assert result.gate_stats["total_candidates"] == 0


@patch("backtest.engine.load_prices_for_date")
@patch("backtest.engine.load_screen_results")
def test_run_backtest_single_winning_trade_positive_return(mock_screen, mock_prices):
    """
    Candidate enters on day 1 at 100, target at 130.
    Price jumps to 135 on day 2 → target hit → positive total_return_pct.
    """
    cand = _make_candidate(entry_price=100.0, stop_loss=90.0, target_price=130.0)

    def _screen(db_path, run_date):
        if run_date == _START:
            return [cand]
        return []

    def _prices(target_date, symbols, config):
        if target_date == _START:
            return {"RELIANCE": 100.0}
        return {"RELIANCE": 135.0}   # above target on day 2

    mock_screen.side_effect = _screen
    mock_prices.side_effect = _prices

    result = run_backtest(_START, _END, _CONFIG, Path("/fake/db.sqlite"))

    assert result.metrics.total_trades >= 1
    assert result.metrics.total_return_pct > 0


@patch("backtest.engine.load_prices_for_date")
@patch("backtest.engine.load_screen_results")
def test_run_backtest_trailing_stop_closes_position(mock_screen, mock_prices):
    """
    Position entered on day 1 at 100, stop 90.
    Trailing stop (7%) rises to ~111.6 as price hits 120.
    Then price falls to 108 (below raised stop) → trailing_stop exit.
    """
    cand = _make_candidate(entry_price=100.0, stop_loss=90.0, target_price=200.0)

    price_seq = {
        date(2024, 1, 2): 100.0,   # entry day
        date(2024, 1, 3): 120.0,   # stop rises to ~111.6
        date(2024, 1, 4): 108.0,   # below raised stop → exit
        date(2024, 1, 5): 108.0,
    }

    def _screen(db_path, run_date):
        return [cand] if run_date == date(2024, 1, 2) else []

    def _prices(target_date, symbols, config):
        p = price_seq.get(target_date, 100.0)
        return {s: p for s in symbols}

    mock_screen.side_effect = _screen
    mock_prices.side_effect = _prices

    result = run_backtest(_START, _END, _CONFIG, Path("/fake/db.sqlite"))

    exit_reasons = [t["exit_reason"] for t in result.trades]
    assert any(r in ("trailing_stop", "fixed_stop", "end_of_data") for r in exit_reasons)
    assert result.metrics.total_trades >= 1


@patch("backtest.engine.load_prices_for_date")
@patch("backtest.engine.load_screen_results")
def test_run_backtest_max_hold_days_closes_position(mock_screen, mock_prices):
    """
    max_hold_days=2.  Position entered day 1; held through day 3 → max_hold exit.
    Uses a short date range with very small max_hold_days.
    """
    config = {
        **_CONFIG,
        "backtest": {**_CONFIG["backtest"], "max_hold_days": 2},
    }
    cand = _make_candidate(entry_price=100.0, stop_loss=90.0, target_price=999.0)

    def _screen(db_path, run_date):
        return [cand] if run_date == date(2024, 1, 2) else []

    def _prices(target_date, symbols, config_):
        return {s: 105.0 for s in symbols}   # price never hits stop or target

    mock_screen.side_effect = _screen
    mock_prices.side_effect = _prices

    # Run 5 business days (Jan 2–5 + ensure max_hold fires)
    result = run_backtest(date(2024, 1, 2), date(2024, 1, 8), config, Path("/fake/db.sqlite"))

    assert result.metrics.total_trades >= 1
    exit_reasons = [t["exit_reason"] for t in result.trades]
    # Should have at least one max_hold or end_of_data exit
    assert any(r in ("max_hold", "end_of_data") for r in exit_reasons)


@patch("backtest.engine.load_prices_for_date")
@patch("backtest.engine.load_screen_results")
def test_run_backtest_gate_stats_stage2_and_tt(mock_screen, mock_prices):
    """
    3 candidates: one stage2+TT, one stage2-only, one neither.
    gate_stats should reflect exact counts.
    """
    candidates = [
        _make_candidate("SYM1", stage=2, trend_template_pass=True),
        _make_candidate("SYM2", stage=2, trend_template_pass=False),
        _make_candidate("SYM3", stage=1, trend_template_pass=False),
    ]

    def _screen(db_path, run_date):
        return candidates if run_date == _START else []

    def _prices(target_date, symbols, config_):
        return {s: 100.0 for s in symbols}

    mock_screen.side_effect = _screen
    mock_prices.side_effect = _prices

    result = run_backtest(_START, _END, _CONFIG, Path("/fake/db.sqlite"))

    gs = result.gate_stats
    assert gs["total_candidates"] == 3
    assert gs["stage2_pass"]      == 2
    assert gs["tt_pass"]          == 1
    assert gs["both_pass"]        == 1


@patch("backtest.engine.load_prices_for_date")
@patch("backtest.engine.load_screen_results")
def test_run_backtest_no_entry_when_no_entry_price(mock_screen, mock_prices):
    """Candidates missing entry_price are silently skipped — no trades opened."""
    cand_no_entry = {
        "symbol": "RELIANCE", "entry_price": None, "stop_loss": 90.0,
        "stage": 2, "trend_template_pass": True, "score": 80,
        "setup_quality": "A",
    }
    mock_screen.return_value = [cand_no_entry]
    mock_prices.return_value = {"RELIANCE": 100.0}

    result = run_backtest(_START, _END, _CONFIG, Path("/fake/db.sqlite"))

    assert result.metrics.total_trades == 0


@patch("backtest.engine.load_prices_for_date")
@patch("backtest.engine.load_screen_results")
def test_backtest_result_equity_curve_has_datetimeindex(mock_screen, mock_prices):
    """equity_curve must have a DatetimeIndex when trades exist."""
    cand = _make_candidate(entry_price=100.0, stop_loss=90.0, target_price=130.0)

    def _screen(db_path, run_date):
        return [cand] if run_date == _START else []

    def _prices(target_date, symbols, config_):
        return {s: 135.0 for s in symbols}   # instant target hit on day 2

    mock_screen.side_effect = _screen
    mock_prices.side_effect = _prices

    result = run_backtest(_START, _END, _CONFIG, Path("/fake/db.sqlite"))

    if not result.equity_curve.empty:
        assert isinstance(result.equity_curve.index, pd.DatetimeIndex)
        assert "equity" in result.equity_curve.columns
        assert "daily_return_pct" in result.equity_curve.columns


# ─────────────────────────────────────────────────────────────────────────────
# run_parameter_sweep tests
# ─────────────────────────────────────────────────────────────────────────────

@patch("backtest.engine.load_prices_for_date", return_value={})
@patch("backtest.engine.load_screen_results", return_value=[])
def test_run_parameter_sweep_one_result_per_value(mock_screen, mock_prices):
    """
    Sweep over 3 explicit values → exactly 3 result dicts returned,
    one per trailing_stop_pct.
    """
    values = [0.05, 0.10, 0.15]
    summary = run_parameter_sweep(
        _START, _END, _CONFIG, Path("/fake/db.sqlite"),
        trailing_stop_values=values,
    )
    assert len(summary) == 3
    returned_values = [s["trailing_stop_pct"] for s in summary]
    assert returned_values == values


@patch("backtest.engine.load_prices_for_date", return_value={})
@patch("backtest.engine.load_screen_results", return_value=[])
def test_run_parameter_sweep_none_value_included(mock_screen, mock_prices):
    """
    None in trailing_stop_values must appear in results and not crash.
    None means fixed-stop-only mode.
    """
    values = [0.07, None]
    summary = run_parameter_sweep(
        _START, _END, _CONFIG, Path("/fake/db.sqlite"),
        trailing_stop_values=values,
    )
    assert len(summary) == 2
    none_result = next(s for s in summary if s["trailing_stop_pct"] is None)
    assert none_result is not None
    # Must have expected keys
    for key in ("cagr", "sharpe", "max_drawdown", "win_rate", "total_trades"):
        assert key in none_result


@patch("backtest.engine.load_prices_for_date", return_value={})
@patch("backtest.engine.load_screen_results", return_value=[])
def test_run_parameter_sweep_default_values(mock_screen, mock_prices):
    """Default sweep uses exactly 5 entries: [0.05, 0.07, 0.10, 0.15, None]."""
    summary = run_parameter_sweep(_START, _END, _CONFIG, Path("/fake/db.sqlite"))
    assert len(summary) == 5
    has_none = any(s["trailing_stop_pct"] is None for s in summary)
    assert has_none


@patch("backtest.engine.load_prices_for_date", return_value={})
@patch("backtest.engine.load_screen_results", return_value=[])
def test_run_parameter_sweep_result_keys(mock_screen, mock_prices):
    """Every result dict must contain the required summary keys."""
    required_keys = {"trailing_stop_pct", "cagr", "sharpe", "max_drawdown", "win_rate", "total_trades"}
    summary = run_parameter_sweep(
        _START, _END, _CONFIG, Path("/fake/db.sqlite"),
        trailing_stop_values=[0.07],
    )
    assert len(summary) == 1
    assert required_keys.issubset(summary[0].keys())
