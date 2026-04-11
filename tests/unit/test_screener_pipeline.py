"""
tests/unit/test_screener_pipeline.py
──────────────────────────────────────
Unit tests for screener/pipeline.py.

Tests are split into two layers:
  1. _screen_single — the per-symbol worker (called directly, no subprocesses)
  2. run_screen     — the orchestrator (ProcessPoolExecutor mocked so tests
                      run quickly and deterministically)

Fixtures build minimal feature DataFrames that satisfy all column
requirements for the rules layer.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from rules.scorer import SEPAResult
from screener.pipeline import _screen_single, run_screen


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

RUN_DATE = datetime.date(2024, 1, 15)


def _stage4_row() -> dict:
    """Row values that produce a clear Stage 4 (decline) — fails Stage 2 gate."""
    return {
        # prices below all MAs → stage 4 territory
        "close":         50.0,
        "SMA_50":       120.0,
        "SMA_150":      130.0,
        "SMA_200":      140.0,
        "MA_slope_200":  -0.5,
        "high_52w":     160.0,
        "low_52w":       45.0,
        "RS_rating":     20,
        # VCP columns
        "vcp_is_valid":          False,
        "vcp_contraction_count": 0,
        "vcp_max_depth_pct":     0.0,
        "vcp_final_depth_pct":   0.0,
        "vcp_vol_ratio":         1.0,
        "vcp_base_weeks":        0,
        "vcp_fail_reason":       "stage != 2",
        # Volume / entry
        "volume":          1_000_000.0,
        "vol_50d_avg":     1_000_000.0,
        "last_pivot_high": np.nan,
        # Stage slope columns
        "MA_slope_50":   -0.3,
        "ema_21":         48.0,
    }


def _stage2_row() -> dict:
    """Row values that pass the Stage 2 gate and TT but do NOT trigger entry."""
    return {
        "close":        155.0,
        "SMA_50":       140.0,
        "SMA_150":      130.0,
        "SMA_200":      120.0,
        "MA_slope_200":   0.3,
        "high_52w":     170.0,
        "low_52w":      100.0,
        "RS_rating":     85,
        "vcp_is_valid":          True,
        "vcp_contraction_count": 3,
        "vcp_max_depth_pct":    12.0,
        "vcp_final_depth_pct":   4.0,
        "vcp_vol_ratio":         0.45,
        "vcp_base_weeks":       10,
        "vcp_fail_reason":      None,
        "volume":      2_000_000.0,
        "vol_50d_avg": 1_000_000.0,
        "last_pivot_high": 160.0,   # close (155) < pivot → no breakout
        "MA_slope_50":   0.4,
        "ema_21":        152.0,
    }


def _make_feature_df(row_dict: dict, n_rows: int = 1) -> pd.DataFrame:
    """
    Build a DataFrame with a date index, repeating the given row n_rows times.
    parquet_store.read() requires a 'date' column or index.
    """
    import datetime as _dt
    base = _dt.date(2024, 1, 1)
    dates = [pd.Timestamp(base) + pd.Timedelta(days=i) for i in range(n_rows)]
    df = pd.DataFrame([row_dict] * n_rows, index=dates)
    df.index.name = "date"
    return df


def _minimal_config(features_dir: Path) -> dict:
    return {
        "data": {"features_dir": str(features_dir)},
        "pipeline": {"n_workers": 1},
        "rules": {
            "stage": {
                "close_above_sma50": True,
                "close_above_sma150": True,
                "close_above_sma200": True,
                "sma50_above_sma150": True,
                "sma150_above_sma200": True,
                "sma200_rising": True,
            },
            "trend_template": {
                "min_conditions_met": 6,
            },
            "entry_trigger": {
                "vol_multiplier": 1.5,
                "lookback_days":  50,
            },
            "stop_loss": {
                "atr_multiplier": 2.0,
                "method": "atr",
            },
            "vcp": {
                "max_contractions": 5,
            },
            "scorer": {},
        },
    }

# ─────────────────────────────────────────────────────────────────────────────
# _screen_single — worker function tests
# ─────────────────────────────────────────────────────────────────────────────

class TestScreenSingleReturnsSepaResult:
    def test_returns_sepa_result_for_stage4_stock(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        df = _make_feature_df(_stage4_row())
        df.to_parquet(feat_dir / "XYZ.parquet", index=True)

        result = _screen_single("XYZ", RUN_DATE, _minimal_config(feat_dir))
        assert isinstance(result, SEPAResult)

    def test_stage4_stock_has_stage_not_2(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        df = _make_feature_df(_stage4_row())
        df.to_parquet(feat_dir / "XYZ.parquet", index=True)

        result = _screen_single("XYZ", RUN_DATE, _minimal_config(feat_dir))
        assert result.stage != 2

    def test_stage4_stock_setup_quality_is_fail(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        df = _make_feature_df(_stage4_row())
        df.to_parquet(feat_dir / "XYZ.parquet", index=True)

        result = _screen_single("XYZ", RUN_DATE, _minimal_config(feat_dir))
        assert result.setup_quality == "FAIL"

    def test_returns_none_when_feature_file_missing(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        # No parquet file written

        result = _screen_single("MISSING", RUN_DATE, _minimal_config(feat_dir))
        assert result is None

    def test_returns_none_when_feature_file_empty(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        pd.DataFrame().to_parquet(feat_dir / "EMPTY.parquet", index=False)

        result = _screen_single("EMPTY", RUN_DATE, _minimal_config(feat_dir))
        assert result is None

    def test_symbol_field_matches_input(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        df = _make_feature_df(_stage4_row())
        df.to_parquet(feat_dir / "MYSTOCK.parquet", index=True)

        result = _screen_single("MYSTOCK", RUN_DATE, _minimal_config(feat_dir))
        assert result.symbol == "MYSTOCK"

    def test_date_field_matches_run_date(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        df = _make_feature_df(_stage4_row())
        df.to_parquet(feat_dir / "XYZ.parquet", index=True)

        result = _screen_single("XYZ", RUN_DATE, _minimal_config(feat_dir))
        assert result.date == RUN_DATE

    def test_stage2_stock_passes_stage_gate(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        df = _make_feature_df(_stage2_row())
        df.to_parquet(feat_dir / "GOOD.parquet", index=True)

        result = _screen_single("GOOD", RUN_DATE, _minimal_config(feat_dir))
        assert result is not None
        assert result.stage == 2

    def test_score_is_non_negative(self, tmp_path):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir()
        df = _make_feature_df(_stage4_row())
        df.to_parquet(feat_dir / "XYZ.parquet", index=True)

        result = _screen_single("XYZ", RUN_DATE, _minimal_config(feat_dir))
        assert result.score >= 0


# ─────────────────────────────────────────────────────────────────────────────
# run_screen — orchestrator tests (ProcessPoolExecutor mocked)
# ─────────────────────────────────────────────────────────────────────────────

def _fake_screen_single(symbol, run_date, config) -> SEPAResult:
    """Stand-in worker that returns a deterministic SEPAResult."""
    return SEPAResult(
        symbol=symbol,
        date=run_date,
        stage=2,
        stage_label="Stage 2",
        stage_confidence=80,
        trend_template_pass=True,
        trend_template_details={f"C{i}": True for i in range(1, 9)},
        conditions_met=7,
        vcp_qualified=True,
        vcp_grade="A",
        vcp_details={"contraction_count": 3},
        breakout_triggered=False,
        entry_price=None,
        stop_loss=None,
        stop_type=None,
        risk_pct=None,
        rs_rating=80,
        setup_quality="A",
        score=80.0,
    )


class TestRunScreen:
    def test_returns_list(self, tmp_path):
        config = _minimal_config(tmp_path / "features")
        with patch("screener.pipeline._screen_single", side_effect=_fake_screen_single):
            with patch("screener.pipeline.ProcessPoolExecutor") as MockExec:
                _setup_mock_executor(MockExec, ["AAA", "BBB"])
                results = run_screen(["AAA", "BBB"], RUN_DATE, config)
        assert isinstance(results, list)

    def test_result_count_matches_universe(self, tmp_path):
        config = _minimal_config(tmp_path / "features")
        universe = ["AAA", "BBB", "CCC"]
        with patch("screener.pipeline._screen_single", side_effect=_fake_screen_single):
            with patch("screener.pipeline.ProcessPoolExecutor") as MockExec:
                _setup_mock_executor(MockExec, universe)
                results = run_screen(universe, RUN_DATE, config)
        assert len(results) == len(universe)

    def test_results_sorted_by_score_descending(self, tmp_path):
        config = _minimal_config(tmp_path / "features")

        def _varied_worker(symbol, run_date, config):
            scores = {"A": 90.0, "B": 70.0, "C": 50.0}
            r = _fake_screen_single(symbol, run_date, config)
            r.score = scores.get(symbol, 60.0)
            return r

        universe = ["B", "C", "A"]
        with patch("screener.pipeline._screen_single", side_effect=_varied_worker):
            with patch("screener.pipeline.ProcessPoolExecutor") as MockExec:
                _setup_mock_executor(MockExec, universe)
                results = run_screen(universe, RUN_DATE, config)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_universe_returns_empty_list(self, tmp_path):
        config = _minimal_config(tmp_path / "features")
        with patch("screener.pipeline.ProcessPoolExecutor") as MockExec:
            _setup_mock_executor(MockExec, [])
            results = run_screen([], RUN_DATE, config)
        assert results == []

    def test_none_result_from_worker_is_excluded(self, tmp_path):
        config = _minimal_config(tmp_path / "features")

        def _worker_with_none(symbol, run_date, config):
            if symbol == "BAD":
                return None
            return _fake_screen_single(symbol, run_date, config)

        universe = ["GOOD", "BAD"]
        with patch("screener.pipeline._screen_single", side_effect=_worker_with_none):
            with patch("screener.pipeline.ProcessPoolExecutor") as MockExec:
                _setup_mock_executor(MockExec, universe)
                results = run_screen(universe, RUN_DATE, config)
        assert all(r is not None for r in results)
        assert not any(r.symbol == "BAD" for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build a mock ProcessPoolExecutor whose futures resolve via
# _screen_single (patched at call site) synchronously.
# ─────────────────────────────────────────────────────────────────────────────

def _setup_mock_executor(MockExec, universe: list[str]):
    """
    Make ProcessPoolExecutor behave as a synchronous map so tests
    don't spin up real worker processes.
    """
    from concurrent.futures import Future

    def _fake_submit(fn, symbol, run_date, config):
        f = Future()
        try:
            # fn here is _screen_single (possibly patched)
            f.set_result(fn(symbol, run_date, config))
        except Exception as exc:
            f.set_exception(exc)
        return f

    mock_instance = MagicMock()
    mock_instance.__enter__ = MagicMock(return_value=mock_instance)
    mock_instance.__exit__ = MagicMock(return_value=False)
    mock_instance.submit.side_effect = _fake_submit

    # as_completed must yield the futures in submission order
    def _as_completed_patch(futures_dict):
        yield from futures_dict.keys()

    MockExec.return_value = mock_instance

    import screener.pipeline as _mod
    _mod_as_completed_orig = _mod.as_completed

    with patch.object(_mod, "as_completed", side_effect=_as_completed_patch):
        pass  # patch applied inside test body — see note below

    # We can't use nested patch here easily; instead we rely on the fact
    # that as_completed is imported at module level and can be patched
    # directly on the module.  The actual patching happens in each test's
    # `with patch(...)` block via the mock executor's futures dict iteration.
