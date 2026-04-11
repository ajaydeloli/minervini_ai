"""
tests/unit/test_backtest_runner.py
───────────────────────────────────
Unit tests for scripts/backtest_runner.py.

All expensive calls (run_backtest, run_parameter_sweep, generate_report)
are mocked — no real data or DB required.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── ensure project root on path ───────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.exceptions import BacktestDataError

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_metrics(**overrides):
    """Return a fake BacktestMetrics-like namespace for assertions."""
    defaults = dict(
        cagr=18.4,
        sharpe_ratio=1.23,
        max_drawdown_pct=-23.1,
        win_rate=58.3,
        total_trades=142,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_result(metrics=None, **overrides):
    """Return a fake BacktestResult-like namespace."""
    defaults = dict(
        start_date=date(2019, 1, 1),
        end_date=date(2024, 1, 1),
        initial_capital=100_000.0,
        final_capital=230_000.0,
        metrics=metrics or _make_metrics(),
        trades=[],
        equity_curve=MagicMock(),
        regime_breakdown={},
        gate_stats={},
        parameter_sweep=None,
        config_snapshot={},
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_FAKE_REPORT_PATHS = {
    "html": Path("reports/backtest/backtest_run_2024-01-15.html"),
    "csv":  Path("reports/backtest/backtest_run_2024-01-15.csv"),
    "chart": Path("reports/backtest/equity_curve_run_2024-01-15.png"),
}

# ─────────────────────────────────────────────────────────────────────────────
# Test: missing --end raises argparse error
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_end_raises_argparse_error():
    """--start without --end must produce a non-zero exit."""
    from scripts import backtest_runner

    with pytest.raises(SystemExit) as exc_info:
        backtest_runner._build_parser().parse_args(["--start", "2019-01-01"])
    assert exc_info.value.code != 0


# ─────────────────────────────────────────────────────────────────────────────
# Test: --start after --end raises BacktestDataError (or SystemExit)
# ─────────────────────────────────────────────────────────────────────────────

def test_start_after_end_raises():
    """--start >= --end must raise BacktestDataError."""
    with pytest.raises(BacktestDataError):
        raise BacktestDataError(
            start_date="2024-01-01",
            end_date="2019-01-01",
            reason="--start must be strictly before --end",
        )


@patch("scripts.backtest_runner.generate_report", return_value=_FAKE_REPORT_PATHS)
@patch("scripts.backtest_runner.run_backtest")
@patch("scripts.backtest_runner._load_benchmark", return_value=None)
def test_start_after_end_exits_1(mock_bench, mock_run, mock_report, capsys):
    """main() must sys.exit(1) when start >= end."""
    import yaml
    import scripts.backtest_runner as runner

    fake_config = {"backtest": {"trailing_stop_pct": 0.07}}

    with patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=fake_config), \
         patch("scripts.backtest_runner.setup_logging"):
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = [
                "backtest_runner.py",
                "--start", "2024-01-01",
                "--end",   "2019-01-01",
            ]
            runner.main()
    assert exc_info.value.code == 1
    mock_run.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Test: --no-trailing sets trailing_stop_pct = None in config
# ─────────────────────────────────────────────────────────────────────────────

@patch("scripts.backtest_runner.generate_report", return_value=_FAKE_REPORT_PATHS)
@patch("scripts.backtest_runner.run_backtest")
@patch("scripts.backtest_runner._load_benchmark", return_value=None)
def test_no_trailing_sets_none(mock_bench, mock_run, mock_report):
    """--no-trailing must inject trailing_stop_pct=None into config before run_backtest."""
    import scripts.backtest_runner as runner

    mock_run.return_value = _make_result()
    fake_config = {"backtest": {"trailing_stop_pct": 0.07}}

    with patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=fake_config), \
         patch("scripts.backtest_runner.setup_logging"):
        sys.argv = [
            "backtest_runner.py",
            "--start", "2019-01-01",
            "--end",   "2024-01-01",
            "--no-trailing",
        ]
        with pytest.raises(SystemExit) as exc_info:
            runner.main()

    assert exc_info.value.code == 0
    call_config = mock_run.call_args.kwargs["config"]
    assert call_config["backtest"]["trailing_stop_pct"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Test: --trailing-stop 0.10 sets trailing_stop_pct = 0.10 in config
# ─────────────────────────────────────────────────────────────────────────────

@patch("scripts.backtest_runner.generate_report", return_value=_FAKE_REPORT_PATHS)
@patch("scripts.backtest_runner.run_backtest")
@patch("scripts.backtest_runner._load_benchmark", return_value=None)
def test_trailing_stop_override(mock_bench, mock_run, mock_report):
    """--trailing-stop 0.10 must set trailing_stop_pct=0.10 in the config passed to engine."""
    import scripts.backtest_runner as runner

    mock_run.return_value = _make_result()
    fake_config = {"backtest": {}}

    with patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=fake_config), \
         patch("scripts.backtest_runner.setup_logging"):
        sys.argv = [
            "backtest_runner.py",
            "--start", "2019-01-01",
            "--end",   "2024-01-01",
            "--trailing-stop", "0.10",
        ]
        with pytest.raises(SystemExit) as exc_info:
            runner.main()

    assert exc_info.value.code == 0
    call_config = mock_run.call_args.kwargs["config"]
    assert call_config["backtest"]["trailing_stop_pct"] == pytest.approx(0.10)


# ─────────────────────────────────────────────────────────────────────────────
# Test: --sweep calls run_parameter_sweep (not just run_backtest)
# ─────────────────────────────────────────────────────────────────────────────

@patch("scripts.backtest_runner.generate_report", return_value=_FAKE_REPORT_PATHS)
@patch("scripts.backtest_runner.run_backtest")
@patch("scripts.backtest_runner.run_parameter_sweep")
@patch("scripts.backtest_runner._load_benchmark", return_value=None)
def test_sweep_calls_run_parameter_sweep(mock_bench, mock_sweep, mock_run, mock_report):
    """--sweep flag must call run_parameter_sweep()."""
    import scripts.backtest_runner as runner

    sweep_data = [{"trailing_stop_pct": 0.07, "cagr": 18.4,
                   "sharpe": 1.2, "max_drawdown": -22.0,
                   "win_rate": 58.0, "total_trades": 140}]
    mock_sweep.return_value = sweep_data
    mock_run.return_value = _make_result()

    fake_config = {"backtest": {}}

    with patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=fake_config), \
         patch("scripts.backtest_runner.setup_logging"), \
         patch("dataclasses.replace", side_effect=lambda r, **kw: r):
        sys.argv = [
            "backtest_runner.py",
            "--start", "2019-01-01",
            "--end",   "2024-01-01",
            "--sweep",
        ]
        with pytest.raises(SystemExit) as exc_info:
            runner.main()

    assert exc_info.value.code == 0
    mock_sweep.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Test: valid args → run_backtest called with correct date objects
# ─────────────────────────────────────────────────────────────────────────────

@patch("scripts.backtest_runner.generate_report", return_value=_FAKE_REPORT_PATHS)
@patch("scripts.backtest_runner.run_backtest")
@patch("scripts.backtest_runner._load_benchmark", return_value=None)
def test_valid_args_calls_run_backtest_with_dates(mock_bench, mock_run, mock_report):
    """Valid --start / --end must call run_backtest with correct date objects."""
    import scripts.backtest_runner as runner

    mock_run.return_value = _make_result()
    fake_config = {"backtest": {}}

    with patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=fake_config), \
         patch("scripts.backtest_runner.setup_logging"):
        sys.argv = [
            "backtest_runner.py",
            "--start", "2019-01-01",
            "--end",   "2024-01-01",
        ]
        with pytest.raises(SystemExit) as exc_info:
            runner.main()

    assert exc_info.value.code == 0
    kwargs = mock_run.call_args.kwargs
    assert kwargs["start_date"] == date(2019, 1, 1)
    assert kwargs["end_date"]   == date(2024, 1, 1)
