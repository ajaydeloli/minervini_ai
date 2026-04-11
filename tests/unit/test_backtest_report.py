"""
tests/unit/test_backtest_report.py
────────────────────────────────────
Unit tests for backtest/report.py  (≥ 8 tests).

All tests use in-memory BacktestResult fixtures — no real data files needed.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backtest.engine import BacktestResult
from backtest.metrics import BacktestMetrics
from backtest.report import (
    _render_html,
    _write_csv,
    generate_report,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_trade(symbol="RELIANCE", pnl=500.0, pnl_pct=5.0,
                exit_reason="trailing_stop", quality="A",
                regime="Bull", initial_risk=100.0) -> dict:
    return {
        "symbol":        symbol,
        "entry_date":    date(2024, 1, 10),
        "exit_date":     date(2024, 1, 25),
        "entry_price":   1000.0,
        "exit_price":    1050.0,
        "qty":           10,
        "pnl":           pnl,
        "pnl_pct":       pnl_pct,
        "r_multiple":    round(pnl / initial_risk, 4) if initial_risk else 0.0,
        "exit_reason":   exit_reason,
        "setup_quality": quality,
        "regime":        regime,
        "initial_risk":  initial_risk,
    }


def _make_equity_curve(trades: list[dict], initial_capital: float) -> pd.DataFrame:
    from backtest.metrics import compute_equity_curve
    return compute_equity_curve(trades, initial_capital)


def _make_result(
    trades: list[dict] | None = None,
    parameter_sweep: list[dict] | None = None,
) -> BacktestResult:
    if trades is None:
        trades = [_make_trade(), _make_trade("DIXON", pnl=-200.0, pnl_pct=-2.0,
                                              quality="B", regime="Bear",
                                              initial_risk=80.0)]
    eq = _make_equity_curve(trades, 100_000.0)
    from backtest.metrics import compute_metrics
    metrics = compute_metrics(trades, 100_000.0)
    return BacktestResult(
        start_date       = date(2024, 1, 1),
        end_date         = date(2024, 3, 31),
        initial_capital  = 100_000.0,
        final_capital    = 100_300.0,
        config_snapshot  = {},
        metrics          = metrics,
        trades           = trades,
        equity_curve     = eq,
        regime_breakdown = {
            "Bull": {"total_trades": 1, "winning_trades": 1},
            "Bear": {"total_trades": 1, "winning_trades": 0},
        },
        gate_stats       = {},
        parameter_sweep  = parameter_sweep,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_write_csv_correct_columns(tmp_path):
    """_write_csv must write exactly the canonical 13 columns as headers."""
    result = _make_result()
    p = tmp_path / "trades.csv"
    _write_csv(result, p)
    with p.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert set(reader.fieldnames) >= {
            "symbol", "entry_date", "exit_date", "entry_price", "exit_price",
            "qty", "pnl", "pnl_pct", "r_multiple", "exit_reason",
            "setup_quality", "regime", "initial_risk",
        }


def test_write_csv_all_trades_serialised(tmp_path):
    """_write_csv must emit exactly one row per trade — no missing rows."""
    trades = [_make_trade(f"SYM{i}") for i in range(7)]
    result = _make_result(trades=trades)
    p = tmp_path / "trades.csv"
    _write_csv(result, p)
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 7


def test_write_csv_values_round_trip(tmp_path):
    """PnL and symbol values must survive the CSV round-trip unchanged."""
    result = _make_result()
    p = tmp_path / "trades.csv"
    _write_csv(result, p)
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    symbols = {r["symbol"] for r in rows}
    assert "RELIANCE" in symbols


def test_render_html_contains_section_headers():
    """HTML must include all major section header strings."""
    result = _make_result()
    html = _render_html(result, Path("/nonexistent/chart.png"))
    for phrase in ["Summary Metrics", "Regime Breakdown",
                   "Recent Trades", "Setup Quality"]:
        assert phrase in html, f"Missing section: {phrase}"


def test_render_html_regime_breakdown_present_when_trades_exist():
    """Regime breakdown table must appear and include regime names from the result."""
    result = _make_result()
    html = _render_html(result, Path("/nonexistent/chart.png"))
    assert "Regime Breakdown" in html
    assert "Bull" in html
    assert "Bear" in html


def test_render_html_parameter_sweep_absent_when_none():
    """Parameter sweep section must NOT appear when result.parameter_sweep is None."""
    result = _make_result(parameter_sweep=None)
    html = _render_html(result, Path("/nonexistent/chart.png"))
    assert "Parameter Sweep" not in html


def test_render_html_parameter_sweep_present_when_populated():
    """Parameter sweep section must appear when result.parameter_sweep is a list."""
    sweep = [
        {
            "trailing_stop_pct": 0.07,
            "cagr":              18.5,
            "sharpe":            1.2,
            "max_drawdown":      -12.0,
            "win_rate":          55.0,
            "total_trades":      30,
        },
    ]
    result = _make_result(parameter_sweep=sweep)
    html = _render_html(result, Path("/nonexistent/chart.png"))
    assert "Parameter Sweep" in html


def test_generate_report_returns_correct_keys(tmp_path):
    """generate_report must return a dict with exactly 'html', 'csv', 'chart' keys."""
    result = _make_result()
    paths = generate_report(result, tmp_path, run_label="test")
    assert set(paths.keys()) == {"html", "csv", "chart"}


def test_generate_report_all_files_created_on_disk(tmp_path):
    """All three output files must actually exist on disk after generate_report."""
    result = _make_result()
    paths = generate_report(result, tmp_path, run_label="myrun")
    for key, path in paths.items():
        assert path.exists(), f"{key} file not found on disk: {path}"


def test_generate_report_default_label(tmp_path):
    """When run_label is omitted the file stems must use 'run' as the label."""
    result = _make_result()
    paths = generate_report(result, tmp_path)
    assert "run" in paths["html"].stem
    assert "run" in paths["csv"].stem
    assert "run" in paths["chart"].stem


def test_generate_report_creates_output_dir(tmp_path):
    """generate_report must create output_dir if it does not already exist."""
    nested = tmp_path / "deep" / "nested" / "dir"
    assert not nested.exists()
    result = _make_result()
    generate_report(result, nested, run_label="dirtest")
    assert nested.exists()


def test_render_html_chart_embedded_as_base64(tmp_path):
    """If the chart PNG exists, the HTML must contain a base64 data URI <img> tag."""
    result = _make_result()
    paths = generate_report(result, tmp_path, run_label="b64test")
    html = paths["html"].read_text(encoding="utf-8")
    assert 'data:image/png;base64,' in html


def test_render_html_pnl_colour_classes_present():
    """HTML must contain 'pos' and 'neg' CSS classes used for PnL colouring."""
    trades = [
        _make_trade("WIN", pnl=800.0,  pnl_pct=8.0),
        _make_trade("LOSE", pnl=-300.0, pnl_pct=-3.0, regime="Bear"),
    ]
    result = _make_result(trades=trades)
    html = _render_html(result, Path("/nonexistent/chart.png"))
    assert 'class="pos' in html or "pos" in html
    assert 'class="neg' in html or "neg" in html
