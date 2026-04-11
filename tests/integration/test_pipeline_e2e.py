"""
tests/integration/test_pipeline_e2e.py
───────────────────────────────────────
End-to-end smoke tests for pipeline/runner.run().

I/O mocked at the boundary:
    - ingestion.universe_loader.resolve_symbols  (avoid universe.yaml reads)
    - features.feature_store.needs_bootstrap / update / bootstrap
    - screener.results.persist_results           (sepa_results table)
    - storage.sqlite_store.save_results          (screener_results table)
    - alerts.telegram_alert.TelegramAlert        (no network calls)
    - alerts.email_alert.EmailAlert
    - alerts.webhook_alert.WebhookAlert
    - reports.daily_watchlist.generate_watchlist
    - reports.chart_generator.generate_chart
    - utils.run_meta.get_config_hash, get_git_sha
    - llm.explainer.generate_trade_brief, generate_watchlist_summary

NOT mocked (rule engine must run):
    - rules.stage.detect_stage
    - rules.trend_template.check_trend_template
    - rules.vcp_rules.check_vcp
    - rules.entry_trigger.check_entry_trigger
    - rules.scorer.evaluate
    - screener.pipeline.run_screen  (uses real feature files from disk)
    - storage.sqlite_store.init_db, log_run, finish_run  (data lineage test)

Test matrix:
    1. run() with 3 mock symbols → RunResult with status='success' or 'partial'
    2. run() with dry_run=True skips screening, returns RunResult immediately
    3. run() with scope='watchlist' and empty watchlist returns RunResult (not exception)
    4. run() when screener fails due to FeatureStoreOutOfSyncError → status='partial'
    5. run_history table is populated after a successful run() (data lineage test)
"""

from __future__ import annotations

import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import storage.parquet_store as parquet_store
from ingestion.universe_loader import RunSymbols
from pipeline.context import RunContext
from pipeline.runner import RunResult, run
from storage.sqlite_store import get_run_history, init_db
from utils.exceptions import FeatureStoreOutOfSyncError

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

RUN_DATE = datetime.date(2024, 6, 3)
MOCK_SYMBOLS = ["AAAA", "BBBB", "CCCC"]


# ─────────────────────────────────────────────────────────────────────────────
# Feature-row builder (Stage 4 → FAIL quality → LLM step is skipped)
# Using Stage 4 keeps the test fast: no A+/A results, so generate_trade_brief
# is never called even if the LLM mock is bypassed.
# ─────────────────────────────────────────────────────────────────────────────

def _stage4_row() -> dict:
    """Declining Stage 4 feature row — scores FAIL, exercises hard gate."""
    return {
        "close": 80.0, "open": 82.0, "high": 84.0, "low": 78.0,
        "volume": 800_000.0,
        "SMA_10": 90.0, "SMA_21": 95.0, "SMA_50": 100.0,
        "SMA_150": 110.0, "SMA_200": 120.0, "EMA_21": 95.0,
        "MA_slope_50": -0.10, "MA_slope_200": -0.08,
        "RS_raw": 0.20, "RS_rating": 20.0,
        "ATR_14": 3.0, "ATR_pct": 3.75,
        "vol_50d_avg": 500_000.0, "vol_ratio": 0.5,
        "up_vol_days": 1, "down_vol_days": 4, "acc_dist_score": -0.6,
        "is_swing_high": False, "is_swing_low": False,
        "last_pivot_high": 100.0, "last_pivot_low": 75.0,
        "vcp_contraction_count": 0.0, "vcp_max_depth_pct": 0.0,
        "vcp_final_depth_pct": 0.0, "vcp_vol_ratio": 1.0,
        "vcp_base_weeks": 0.0, "vcp_is_valid": False,
        "vcp_fail_reason": "stage != 2",
        "low_52w": 70.0, "high_52w": 150.0,
    }


def _make_feature_df(row_data: dict, run_date: datetime.date = RUN_DATE) -> pd.DataFrame:
    return pd.DataFrame(
        [row_data],
        index=pd.DatetimeIndex([pd.Timestamp(run_date)], name="date"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Config / context factories
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(features_dir: Path) -> dict:
    return {
        "data": {
            "features_dir": str(features_dir),
            "processed_dir": str(features_dir),  # unused in tests but required by config
        },
        "pipeline": {"n_workers": 1},
        "stage":    {"ma200_slope_lookback": 20, "ma50_slope_lookback": 10},
        "trend_template": {
            "ma200_slope_lookback": 20,
            "pct_above_52w_low": 25.0,
            "pct_below_52w_high": 25.0,
            "min_rs_rating": 70,
        },
        "vcp": {
            "detector": "rule_based",
            "min_contractions": 2, "max_contractions": 5,
            "require_declining_depth": True, "require_vol_contraction": True,
            "min_weeks": 3, "max_weeks": 52,
            "tightness_pct": 10.0, "max_depth_pct": 50.0,
        },
        "scoring": {
            "weights": {
                "rs_rating": 0.30, "trend": 0.25, "vcp": 0.25,
                "volume": 0.10, "fundamental": 0.07, "news": 0.03,
            },
            "setup_quality_thresholds": {"a_plus": 85, "a": 70, "b": 55, "c": 40},
        },
        "entry":        {"breakout_vol_multiplier": 1.5, "pivot_lookback_days": 60},
        "fundamentals": {"enabled": False},
        "news":         {"enabled": False},
        "llm":          {"enabled": False, "only_for_quality": []},
        "paper_trading": {"enabled": False},
        "watchlist":    {"always_generate_charts": False},
        "universe_yaml_path": "config/universe.yaml",
    }


def _make_run_context(
    db_path: Path,
    features_dir: Path,
    scope: str = "all",
    dry_run: bool = False,
) -> RunContext:
    return RunContext(
        run_date=RUN_DATE,
        mode="test",
        scope=scope,
        config=_make_config(features_dir),
        db_path=db_path,
        dry_run=dry_run,
        config_path=Path("config/settings.yaml"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Mock helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_run_symbols(symbols: list[str], scope: str = "all") -> RunSymbols:
    return RunSymbols(
        watchlist=symbols if scope in ("all", "watchlist") else [],
        universe=symbols if scope in ("all", "universe") else [],
        all=symbols,
        scope=scope,
    )


def _mock_alert_result(sent: int = 0, skipped: int = 0) -> MagicMock:
    ar = MagicMock()
    ar.sent    = sent
    ar.skipped = skipped
    return ar


def _mock_watchlist_output() -> MagicMock:
    wo = MagicMock()
    wo.csv_path  = None
    wo.html_path = None
    return wo


@contextmanager
def _standard_io_mocks(
    symbols: list[str] = MOCK_SYMBOLS,
    scope: str = "all",
) -> Generator[None, None, None]:
    """
    Context manager that patches all I/O boundary calls needed for a clean
    run() invocation.  Rule engine and screener.pipeline are NOT patched.
    """
    run_symbols = _mock_run_symbols(symbols, scope)
    with (
        patch("ingestion.universe_loader.resolve_symbols", return_value=run_symbols),
        patch("features.feature_store.needs_bootstrap", return_value=False),
        patch("features.feature_store.update"),
        patch("features.feature_store.bootstrap"),
        patch("screener.results.persist_results"),
        patch("storage.sqlite_store.save_results"),
        patch("utils.run_meta.get_config_hash", return_value="test_hash"),
        patch("utils.run_meta.get_git_sha",     return_value="test_sha"),
        patch("alerts.telegram_alert.TelegramAlert") as mock_tg,
        patch("alerts.email_alert.EmailAlert")    as mock_email,
        patch("alerts.webhook_alert.WebhookAlert") as mock_wh,
        patch("reports.daily_watchlist.generate_watchlist",
              return_value=_mock_watchlist_output()),
        patch("reports.chart_generator.generate_chart"),
        patch("llm.explainer.generate_trade_brief",       return_value=None),
        patch("llm.explainer.generate_watchlist_summary", return_value=None),
    ):
        mock_tg.return_value.send.return_value    = None
        mock_email.return_value.send.return_value  = _mock_alert_result()
        mock_wh.return_value.send.return_value     = _mock_alert_result()
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def env(tmp_path: Path):
    """
    Yields a dict with:
        features_dir : Path  — temp dir with feature parquet files for MOCK_SYMBOLS
        db_path      : Path  — fresh initialised SQLite database
    """
    features_dir = tmp_path / "features"
    features_dir.mkdir()

    # Write Stage-4 feature files so run_screen can read them without crashing.
    # Stage-4 → FAIL quality → LLM / chart steps are skipped → fast tests.
    for sym in MOCK_SYMBOLS:
        path = features_dir / f"{sym}.parquet"
        parquet_store.write(_make_feature_df(_stage4_row()), path, overwrite=True)

    db_path = tmp_path / "test_minervini.db"
    init_db(db_path)

    yield {"features_dir": features_dir, "db_path": db_path}


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — run() with 3 mock symbols returns RunResult with status success/partial
# ─────────────────────────────────────────────────────────────────────────────

class TestRunReturnsRunResult:
    """run() orchestrates all pipeline steps and returns a valid RunResult."""

    def test_run_returns_run_result_with_valid_status(self, env: dict) -> None:
        ctx = _make_run_context(env["db_path"], env["features_dir"])

        with _standard_io_mocks(MOCK_SYMBOLS):
            result = run(ctx)

        assert isinstance(result, RunResult), (
            f"run() must return a RunResult, got {type(result).__name__}"
        )
        assert result.status in ("success", "partial"), (
            f"Expected status 'success' or 'partial', got {result.status!r}"
        )
        assert result.run_date == RUN_DATE
        assert result.duration_sec >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — dry_run=True skips screening and returns RunResult immediately
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunSkipsScreening:
    """dry_run=True must bypass feature computation, run_screen, and alerts."""

    def test_dry_run_returns_run_result(self, env: dict) -> None:
        ctx = _make_run_context(
            env["db_path"], env["features_dir"], dry_run=True
        )

        with _standard_io_mocks(MOCK_SYMBOLS) as _, \
             patch("screener.pipeline.run_screen") as mock_screen:
            result = run(ctx)

        assert isinstance(result, RunResult)
        # run_screen must NOT be called in dry-run mode
        mock_screen.assert_not_called()

    def test_dry_run_status_not_failed(self, env: dict) -> None:
        ctx = _make_run_context(
            env["db_path"], env["features_dir"], dry_run=True
        )

        with _standard_io_mocks(MOCK_SYMBOLS):
            result = run(ctx)

        assert result.status != "failed", (
            "dry_run should not produce a 'failed' status"
        )
        assert result.symbols_screened == 0, (
            "dry_run must not count screened symbols (run_screen was skipped)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — scope='watchlist' with empty watchlist returns RunResult (not exception)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyWatchlistScope:
    """scope='watchlist' with an empty watchlist must not raise — return RunResult."""

    def test_empty_watchlist_returns_run_result(self, env: dict) -> None:
        ctx = _make_run_context(
            env["db_path"], env["features_dir"], scope="watchlist"
        )
        # Simulate an empty watchlist: symbols_to_scan = []
        empty_symbols = _mock_run_symbols([], scope="watchlist")

        with (
            patch("ingestion.universe_loader.resolve_symbols", return_value=empty_symbols),
            patch("features.feature_store.needs_bootstrap", return_value=False),
            patch("features.feature_store.update"),
            patch("features.feature_store.bootstrap"),
            patch("screener.results.persist_results"),
            patch("storage.sqlite_store.save_results"),
            patch("utils.run_meta.get_config_hash", return_value="test_hash"),
            patch("utils.run_meta.get_git_sha",     return_value="test_sha"),
            patch("alerts.telegram_alert.TelegramAlert"),
            patch("alerts.email_alert.EmailAlert"),
            patch("alerts.webhook_alert.WebhookAlert"),
            patch("reports.daily_watchlist.generate_watchlist",
                  return_value=_mock_watchlist_output()),
            patch("reports.chart_generator.generate_chart"),
            patch("llm.explainer.generate_trade_brief",       return_value=None),
            patch("llm.explainer.generate_watchlist_summary", return_value=None),
        ):
            result = run(ctx)

        assert isinstance(result, RunResult), (
            "run() must return a RunResult even when the watchlist scope is empty"
        )
        assert result.symbols_screened == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — FeatureStoreOutOfSyncError on all symbols → status='partial'
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureStoreOutOfSyncReturnsPartial:
    """
    When every feature store update raises FeatureStoreOutOfSyncError AND
    run_screen subsequently fails (simulating a fully stale feature store),
    the pipeline must return status='partial' rather than crashing.

    Mechanism: step 4 logs warnings for per-symbol FeatureStoreOutOfSyncError;
    step 5 catches the run_screen exception and sets status='partial'.
    """

    def test_all_symbols_out_of_sync_returns_partial(self, env: dict) -> None:
        ctx = _make_run_context(env["db_path"], env["features_dir"])

        with (
            patch("ingestion.universe_loader.resolve_symbols",
                  return_value=_mock_run_symbols(MOCK_SYMBOLS)),
            patch("features.feature_store.needs_bootstrap", return_value=False),
            # Step 4: all updates raise FeatureStoreOutOfSyncError
            patch("features.feature_store.update",
                  side_effect=FeatureStoreOutOfSyncError("AAAA", str(RUN_DATE))),
            patch("features.feature_store.bootstrap"),
            # Step 5: simulate run_screen failing because feature store is stale
            patch("screener.pipeline.run_screen",
                  side_effect=RuntimeError("All feature files out of sync")),
            patch("screener.results.persist_results"),
            patch("storage.sqlite_store.save_results"),
            patch("utils.run_meta.get_config_hash", return_value="test_hash"),
            patch("utils.run_meta.get_git_sha",     return_value="test_sha"),
            patch("alerts.telegram_alert.TelegramAlert"),
            patch("alerts.email_alert.EmailAlert"),
            patch("alerts.webhook_alert.WebhookAlert"),
            patch("reports.daily_watchlist.generate_watchlist",
                  return_value=_mock_watchlist_output()),
            patch("reports.chart_generator.generate_chart"),
            patch("llm.explainer.generate_trade_brief",       return_value=None),
            patch("llm.explainer.generate_watchlist_summary", return_value=None),
        ):
            result = run(ctx)

        assert result.status == "partial", (
            f"Expected status='partial' when all feature stores are out of sync, "
            f"got {result.status!r}"
        )
        assert isinstance(result, RunResult)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — run_history populated after a successful run() (data lineage test)
# ─────────────────────────────────────────────────────────────────────────────

class TestRunHistoryDataLineage:
    """
    After a successful run(), the run_history table must contain at least one
    row for that run_date with status 'success' or 'partial'.

    This test validates the data lineage contract: every pipeline run is
    auditable via run_history.log_run() + finish_run() (steps 3 and 12).
    """

    def test_run_history_populated_after_successful_run(self, env: dict) -> None:
        ctx = _make_run_context(env["db_path"], env["features_dir"])

        with _standard_io_mocks(MOCK_SYMBOLS):
            result = run(ctx)

        # init_db was called inside run() and set the module-level db path.
        # get_run_history() now queries that same database.
        history = get_run_history(limit=10)

        assert history, (
            "run_history must contain at least one row after a successful run()"
        )

        # The most recent row should match our run_date
        latest = history[0]
        assert latest["run_date"] == str(RUN_DATE), (
            f"Expected run_date={RUN_DATE!s} in run_history, "
            f"got {latest['run_date']!r}"
        )
        assert latest["status"] in ("success", "partial", "running"), (
            f"Unexpected status {latest['status']!r} in run_history"
        )
        assert result.status in ("success", "partial"), (
            f"RunResult.status should be success or partial, got {result.status!r}"
        )
