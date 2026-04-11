"""
tests/integration/test_screener_batch.py
─────────────────────────────────────────
Integration tests for screener/pipeline.run_screen() in batch mode.

Fixture files (sample_ohlcv.parquet) are used to build synthetic feature
rows for 3-5 symbols.  All parquet files are written to a pytest tmp_path
directory; no permanent filesystem state is created.

Tests
─────
    1. run_screen() returns a list of SEPAResult objects (may be empty)
    2. Results are sorted by score descending
    3. A symbol forced into Stage 4 data is absent from actionable results
       (hard gate: receives FAIL quality / score=0, never A/B/C)
    4. n_workers=1 and n_workers=2 produce the same results (parallelism stability)
    5. If all symbols fail processing (missing feature files), run_screen
       returns an empty list — not an exception
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd
import pytest

import storage.parquet_store as parquet_store
from rules.scorer import SEPAResult
from screener.pipeline import run_screen

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

RUN_DATE      = datetime.date(2024, 6, 3)
SYMBOLS       = ["ALPHA", "BETA", "GAMMA"]
STAGE4_SYMBOL = "DELTA"


# ─────────────────────────────────────────────────────────────────────────────
# Feature-row builders
# ─────────────────────────────────────────────────────────────────────────────

def _stage2_row(**overrides) -> dict:
    """
    Synthetic feature row that satisfies all Stage 2 conditions.
    Mirrors _feature_row_all_tt_pass() from test_known_setups.py plus
    the additional columns required by entry_trigger and scorer.
    """
    data = {
        # Price / MA stack (Stage 2 gate)
        "close":        150.0,
        "open":         148.0,
        "high":         152.0,
        "low":          147.0,
        "volume":       1_000_000.0,
        "SMA_10":       145.0,
        "SMA_21":       140.0,
        "SMA_50":       130.0,
        "SMA_150":      120.0,
        "SMA_200":      110.0,
        "EMA_21":       140.0,
        "MA_slope_50":  0.10,
        "MA_slope_200": 0.08,
        # Relative strength
        "RS_raw":    0.80,
        "RS_rating": 80.0,
        # ATR
        "ATR_14":  3.0,
        "ATR_pct": 2.0,
        # Volume
        "vol_50d_avg":    500_000.0,
        "vol_ratio":      2.0,
        "up_vol_days":    3,
        "down_vol_days":  2,
        "acc_dist_score": 0.6,
        # Pivot
        "is_swing_high":  False,
        "is_swing_low":   False,
        "last_pivot_high": 148.0,
        "last_pivot_low":  125.0,
        # VCP
        "vcp_contraction_count": 3.0,
        "vcp_max_depth_pct":     20.0,
        "vcp_final_depth_pct":   3.0,
        "vcp_vol_ratio":         0.3,
        "vcp_base_weeks":        10.0,
        "vcp_is_valid":          True,
        "vcp_fail_reason":       None,
        # 52-week range (trend template)
        "low_52w":  100.0,
        "high_52w": 170.0,
    }
    data.update(overrides)
    return data


def _stage4_row(**overrides) -> dict:
    """Synthetic feature row in Stage 4 (declining): close below both MAs, both slopes negative."""
    data = {
        "close":        80.0,
        "open":         82.0,
        "high":         84.0,
        "low":          78.0,
        "volume":       800_000.0,
        "SMA_10":       90.0,
        "SMA_21":       95.0,
        "SMA_50":       100.0,
        "SMA_150":      110.0,
        "SMA_200":      120.0,
        "EMA_21":       95.0,
        "MA_slope_50":  -0.10,
        "MA_slope_200": -0.08,
        "RS_raw":    0.20,
        "RS_rating": 20.0,
        "ATR_14":  3.0,
        "ATR_pct": 3.75,
        "vol_50d_avg":    500_000.0,
        "vol_ratio":      0.5,
        "up_vol_days":    1,
        "down_vol_days":  4,
        "acc_dist_score": -0.6,
        "is_swing_high":  False,
        "is_swing_low":   False,
        "last_pivot_high": 100.0,
        "last_pivot_low":   75.0,
        "vcp_contraction_count": 0.0,
        "vcp_max_depth_pct":     0.0,
        "vcp_final_depth_pct":   0.0,
        "vcp_vol_ratio":         1.0,
        "vcp_base_weeks":        0.0,
        "vcp_is_valid":          False,
        "vcp_fail_reason":       "stage != 2",
        "low_52w":  70.0,
        "high_52w": 150.0,
    }
    data.update(overrides)
    return data


def _make_feature_df(row_data: dict, run_date: datetime.date = RUN_DATE) -> pd.DataFrame:
    """Wrap a single row dict into a 1-row feature DataFrame with a DatetimeIndex."""
    return pd.DataFrame(
        [row_data],
        index=pd.DatetimeIndex([pd.Timestamp(run_date)], name="date"),
    )


def _write_feature(symbol: str, df: pd.DataFrame, features_dir: Path) -> Path:
    """Write *df* as the feature parquet for *symbol* into *features_dir*."""
    path = features_dir / f"{symbol}.parquet"
    parquet_store.write(df, path, overwrite=True)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Config helper
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(features_dir: Path, n_workers: int = 1) -> dict:
    """Minimal config for run_screen() pointing to *features_dir*."""
    return {
        "data": {"features_dir": str(features_dir)},
        "pipeline": {"n_workers": n_workers},
        "stage": {
            "ma200_slope_lookback": 20,
            "ma50_slope_lookback":  10,
        },
        "trend_template": {
            "ma200_slope_lookback": 20,
            "pct_above_52w_low":    25.0,
            "pct_below_52w_high":   25.0,
            "min_rs_rating":        70,
        },
        "vcp": {
            "detector":                "rule_based",
            "min_contractions":        2,
            "max_contractions":        5,
            "require_declining_depth": True,
            "require_vol_contraction": True,
            "min_weeks":               3,
            "max_weeks":               52,
            "tightness_pct":           10.0,
            "max_depth_pct":           50.0,
        },
        "scoring": {
            "weights": {
                "rs_rating":   0.30,
                "trend":       0.25,
                "vcp":         0.25,
                "volume":      0.10,
                "fundamental": 0.07,
                "news":        0.03,
            },
            "setup_quality_thresholds": {
                "a_plus": 85,
                "a":      70,
                "b":      55,
                "c":      40,
            },
        },
        "entry": {
            "breakout_vol_multiplier": 1.5,
            "pivot_lookback_days":     60,
        },
        "fundamentals": {"enabled": False},
        "news":         {"enabled": False},
        "llm":          {"enabled": False, "only_for_quality": []},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Shared pytest fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def features_dir(tmp_path: Path) -> Path:
    fd = tmp_path / "features"
    fd.mkdir()
    return fd


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — run_screen() returns a list of SEPAResult objects
# ─────────────────────────────────────────────────────────────────────────────

class TestRunScreenReturnsSEPAResults:
    """run_screen() always returns list[SEPAResult], including when the universe is empty."""

    def test_returns_list_of_sepa_results(self, features_dir: Path) -> None:
        """3 Stage 2 symbols → returns a non-empty list of SEPAResult objects."""
        config = _make_config(features_dir)
        for sym in SYMBOLS:
            _write_feature(sym, _make_feature_df(_stage2_row()), features_dir)

        results = run_screen(SYMBOLS, RUN_DATE, config, n_workers=1)

        assert isinstance(results, list), "run_screen must return a list"
        for r in results:
            assert isinstance(r, SEPAResult), (
                f"Expected SEPAResult, got {type(r).__name__}"
            )

    def test_empty_universe_returns_empty_list(self, features_dir: Path) -> None:
        """An empty universe must return [] without raising."""
        config = _make_config(features_dir)
        results = run_screen([], RUN_DATE, config, n_workers=1)
        assert results == []


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Results sorted by score descending
# ─────────────────────────────────────────────────────────────────────────────

class TestResultsSortedByScore:
    """Results must be ordered by score descending (highest score first)."""

    def test_results_sorted_descending(self, features_dir: Path) -> None:
        """3 symbols with different RS_rating → results sorted score DESC."""
        config = _make_config(features_dir)
        syms_data = {
            "HIGHRS": _stage2_row(RS_rating=99.0, vol_ratio=2.0),
            "MEDRS":  _stage2_row(RS_rating=75.0, vol_ratio=1.5),
            "LOWRS":  _stage2_row(RS_rating=72.0, vol_ratio=1.0),
        }
        for sym, data in syms_data.items():
            _write_feature(sym, _make_feature_df(data), features_dir)

        results = run_screen(list(syms_data.keys()), RUN_DATE, config, n_workers=1)

        assert len(results) >= 2, "Expected at least 2 results to test sort order"
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True), (
            f"Results are not sorted by score descending: {scores}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Stage 4 hard gate blocks symbol from actionable results
# ─────────────────────────────────────────────────────────────────────────────

class TestStage4HardGate:
    """
    A symbol whose feature row puts it in Stage 4 must receive FAIL quality
    and score=0 — it is absent from the actionable (non-FAIL) results.
    """

    def test_stage4_symbol_receives_fail_quality(self, features_dir: Path) -> None:
        """Stage 4 hard gate: score=0, setup_quality='FAIL'."""
        config = _make_config(features_dir)

        for sym in SYMBOLS:
            _write_feature(sym, _make_feature_df(_stage2_row()), features_dir)
        _write_feature(STAGE4_SYMBOL, _make_feature_df(_stage4_row()), features_dir)

        all_syms = SYMBOLS + [STAGE4_SYMBOL]
        results  = run_screen(all_syms, RUN_DATE, config, n_workers=1)

        stage4_results = [r for r in results if r.symbol == STAGE4_SYMBOL]
        assert stage4_results, (
            f"Stage 4 symbol {STAGE4_SYMBOL!r} was not returned at all — "
            "expected a FAIL SEPAResult, not a missing result"
        )
        for r in stage4_results:
            assert r.setup_quality == "FAIL", (
                f"Stage 4 symbol must receive setup_quality='FAIL', got {r.setup_quality!r}"
            )
            assert r.score == 0, (
                f"Stage 4 symbol must receive score=0 (hard gate), got {r.score}"
            )

    def test_stage4_symbol_absent_from_actionable_results(self, features_dir: Path) -> None:
        """Stage 4 symbol is absent from actionable (non-FAIL) results."""
        config = _make_config(features_dir)

        for sym in SYMBOLS:
            _write_feature(sym, _make_feature_df(_stage2_row()), features_dir)
        _write_feature(STAGE4_SYMBOL, _make_feature_df(_stage4_row()), features_dir)

        results   = run_screen(SYMBOLS + [STAGE4_SYMBOL], RUN_DATE, config, n_workers=1)
        actionable = [r for r in results if r.setup_quality != "FAIL"]

        assert not any(r.symbol == STAGE4_SYMBOL for r in actionable), (
            f"Stage 4 symbol {STAGE4_SYMBOL!r} must not appear in actionable "
            f"(non-FAIL) results. Actionable symbols: {[r.symbol for r in actionable]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Parallelism stability: n_workers=1 vs n_workers=2
# ─────────────────────────────────────────────────────────────────────────────

class TestParallelismStability:
    """n_workers=1 and n_workers=2 must produce identical ranked results."""

    def test_single_vs_dual_workers_same_results(self, features_dir: Path) -> None:
        config_1 = _make_config(features_dir, n_workers=1)
        config_2 = _make_config(features_dir, n_workers=2)

        for sym in SYMBOLS:
            _write_feature(sym, _make_feature_df(_stage2_row()), features_dir)

        results_1 = run_screen(SYMBOLS, RUN_DATE, config_1, n_workers=1)
        results_2 = run_screen(SYMBOLS, RUN_DATE, config_2, n_workers=2)

        assert len(results_1) == len(results_2), (
            f"n_workers=1 returned {len(results_1)} results, "
            f"n_workers=2 returned {len(results_2)}"
        )

        # Results must be in the same order (both are sorted by score DESC)
        pairs = zip(results_1, results_2)
        for r1, r2 in pairs:
            assert r1.symbol == r2.symbol, (
                f"Symbol order differs: workers=1 has {r1.symbol!r}, "
                f"workers=2 has {r2.symbol!r}"
            )
            assert r1.score == r2.score, (
                f"Score differs for {r1.symbol!r}: "
                f"workers=1={r1.score}, workers=2={r2.score}"
            )
            assert r1.setup_quality == r2.setup_quality, (
                f"Quality differs for {r1.symbol!r}: "
                f"workers=1={r1.setup_quality!r}, workers=2={r2.setup_quality!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — All symbols fail processing → empty list (not exception)
# ─────────────────────────────────────────────────────────────────────────────

class TestAllSymbolsFailProcessing:
    """
    When every worker fails to load the feature file (file missing / corrupt),
    _screen_single returns None for all symbols and run_screen returns [].
    The function must not raise an exception.
    """

    def test_missing_feature_files_returns_empty_list(self, features_dir: Path) -> None:
        """All symbols lack feature files → workers fail → run_screen returns []."""
        config = _make_config(features_dir)

        # Intentionally do NOT write any parquet files for these symbols.
        symbols_without_files = ["GHOST1", "GHOST2", "GHOST3"]

        results = run_screen(symbols_without_files, RUN_DATE, config, n_workers=1)

        assert isinstance(results, list), (
            "run_screen must return a list even when all workers fail, not raise"
        )
        assert results == [], (
            f"Expected empty list when all feature files are missing, "
            f"got {len(results)} result(s)"
        )
