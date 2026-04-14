"""
screener/pipeline.py
────────────────────
Orchestrates the full SEPA feature → rules pipeline across the universe
in parallel, returning a ranked list of SEPAResult objects.

Pipeline execution order per symbol
────────────────────────────────────
    1. Load feature file: storage/parquet_store.read(features_dir/{symbol}.parquet)
    2. Take last row: row = df.iloc[-1]
    3. detect_stage(row, config)          → StageResult
    4. If stage != 2: immediately create FAIL SEPAResult, skip steps 5–7
    5. check_trend_template(row, config)  → TrendTemplateResult
    6. check_vcp(row, config)             → VCPQualification
    7. check_entry_trigger(row, config)   → EntryTrigger
    8. If entry triggered: compute_stop_loss(row, entry_price, config) → StopLossResult
    9. evaluate(...)                      → SEPAResult
   10. Return SEPAResult

Parallel execution
──────────────────
Uses concurrent.futures.ProcessPoolExecutor with n_workers from config or
default 4.  Each worker processes one symbol independently.  Failed symbols
are logged as WARNING and skipped — the batch never crashes.

Public API
──────────
    run_screen(universe, run_date, config, n_workers=4) → list[SEPAResult]
    _screen_single(symbol, run_date, config)            → SEPAResult | None
"""

from __future__ import annotations

import datetime
import multiprocessing
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rules.entry_trigger import EntryTrigger, check_entry_trigger
from rules.risk_reward import compute_rr, RRResult
from rules.scorer import SEPAResult, evaluate
from rules.stage import StageResult, detect_stage
from rules.stop_loss import compute_stop_loss
from rules.trend_template import TrendTemplateResult, check_trend_template
from rules.vcp_rules import VCPQualification, check_vcp
from utils.logger import get_logger

import storage.parquet_store as parquet_store

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Sentinel dataclasses for non-Stage-2 short-circuit
# ─────────────────────────────────────────────────────────────────────────────

def _null_tt_result() -> TrendTemplateResult:
    """Empty TrendTemplateResult used when Stage 2 gate fails."""
    return TrendTemplateResult(
        passes=False,
        conditions={f"C{i}": False for i in range(1, 9)},
        conditions_met=0,
        details={},
    )


def _null_vcp_result() -> VCPQualification:
    """Empty VCPQualification used when Stage 2 gate fails."""
    return VCPQualification(
        qualified=False,
        contraction_count=0,
        max_depth_pct=0.0,
        final_depth_pct=0.0,
        vol_ratio=1.0,
        base_weeks=0,
        fail_reason="stage != 2",
        quality_grade="FAIL",
    )


def _null_entry_trigger() -> EntryTrigger:
    """Empty EntryTrigger used when Stage 2 gate fails."""
    return EntryTrigger(
        triggered=False,
        entry_price=None,
        pivot_high=None,
        breakout_vol_ratio=None,
        volume_confirmed=False,
        reason="stage != 2",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Worker function (runs in subprocess)
# ─────────────────────────────────────────────────────────────────────────────

def _screen_single(
    symbol: str,
    run_date: datetime.date,
    config: dict[str, Any],
) -> SEPAResult | None:
    """
    Process one symbol through the full SEPA pipeline.

    This is the worker function executed in each subprocess.  It is a
    module-level function (not a lambda or closure) so that
    ProcessPoolExecutor can pickle it.

    Parameters
    ──────────
    symbol   : NSE symbol string.
    run_date : Evaluation date (most recent trading day).
    config   : Full application configuration dict.

    Returns
    ───────
    SEPAResult on success (including FAIL results for non-Stage-2 stocks).
    None on any exception — the exception is logged and the symbol is skipped.
    """
    # Worker processes inherit no handlers — set up logging minimally.
    from utils.logger import setup_logging
    setup_logging()

    worker_log = get_logger(__name__)

    try:
        features_dir = Path(config["data"]["features_dir"])
        feature_path = features_dir / f"{symbol}.parquet"

        # ── Step 1–2: load feature file, take last row ────────────────────
        df = parquet_store.read(feature_path)
        if df.empty:
            worker_log.warning("Feature file is empty, skipping", symbol=symbol)
            return None

        row = df.iloc[-1]

        # ── Step 3: stage detection (hard gate) ──────────────────────────
        stage_result: StageResult = detect_stage(row, config)

        if stage_result.stage != 2:
            # ── Step 4: immediate FAIL — skip TT, VCP, entry ─────────────
            return evaluate(
                symbol=symbol,
                date=run_date,
                row=row,
                stage_result=stage_result,
                tt_result=_null_tt_result(),
                vcp_qual=_null_vcp_result(),
                entry_trigger=_null_entry_trigger(),
                stop_result=None,
                config=config,
            )

        # ── Step 5: trend template ────────────────────────────────────────
        tt_result: TrendTemplateResult = check_trend_template(row, config)

        # ── Step 6: VCP qualification ─────────────────────────────────────
        vcp_qual: VCPQualification = check_vcp(row, config)

        # ── Step 7: entry trigger ─────────────────────────────────────────
        entry_trigger: EntryTrigger = check_entry_trigger(row, config)

        # ── Step 8: stop-loss (only when entry triggered) ─────────────────
        stop_result = None
        if entry_trigger.triggered and entry_trigger.entry_price is not None:
            stop_result = compute_stop_loss(row, entry_trigger.entry_price, config)

        # ── Step 8b: Reward:Risk (only when stop-loss was computed) ──────
        rr_result: RRResult | None = None
        if stop_result is not None and entry_trigger.entry_price is not None:
            try:
                rr_result = compute_rr(
                    row,
                    entry_trigger.entry_price,
                    stop_result.stop_price,
                    config,
                )
            except Exception as _rr_exc:
                worker_log.warning(
                    "compute_rr failed — rr_result=None",
                    symbol=symbol,
                    error=str(_rr_exc),
                )

        # ── Step 8c: Fundamentals (only for Stage 2 stocks) ─────────────────
        fundamental_result = None
        if config.get("fundamentals", {}).get("enabled", False):
            try:
                from ingestion.fundamentals import fetch_fundamentals
                from rules.fundamental_template import check_fundamental_template
                fund_data = fetch_fundamentals(symbol, config)
                fundamental_result = check_fundamental_template(fund_data, config)
            except Exception as _fund_exc:
                worker_log.warning(
                    "Fundamentals failed — skipping",
                    symbol=symbol,
                    error=str(_fund_exc),
                )

        # ── Step 8d: News sentiment (only for Stage 2 stocks) ───────────────
        news_score_val = None
        if config.get("news", {}).get("enabled", False):
            try:
                from ingestion.news import fetch_symbol_news, compute_news_score
                articles = fetch_symbol_news(symbol, config)
                news_score_val = compute_news_score(articles)
            except Exception as _news_exc:
                worker_log.warning(
                    "News scoring failed — skipping",
                    symbol=symbol,
                    error=str(_news_exc),
                )

        # ── Step 9: composite evaluation ──────────────────────────────────
        return evaluate(
            symbol=symbol,
            date=run_date,
            row=row,
            stage_result=stage_result,
            tt_result=tt_result,
            vcp_qual=vcp_qual,
            entry_trigger=entry_trigger,
            stop_result=stop_result,
            config=config,
            rr_result=rr_result,
            fundamental_result=fundamental_result,
            news_score=news_score_val,
        )

    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "Symbol processing failed — skipped",
            symbol=symbol,
            error=str(exc),
            exc_info=True,
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_screen(
    universe: list[str],
    run_date: datetime.date,
    config: dict[str, Any],
    n_workers: int = 4,
) -> list[SEPAResult]:
    """
    Run the SEPA screen across *universe* in parallel and return results.

    Parameters
    ──────────
    universe  : List of NSE symbol strings to screen.
    run_date  : Evaluation date (most recent trading day).
    config    : Full application configuration dict (settings.yaml).
    n_workers : Number of worker processes.  Defaults to 4, overridden by
                config["pipeline"]["n_workers"] when present.

    Returns
    ───────
    list[SEPAResult] sorted by score descending (highest first).
    Failed symbols (any exception in the worker) are silently skipped.

    Logs
    ────
    INFO — symbols_processed, passed_stage2, passed_tt, vcp_qualified,
           breakout_triggered, time_taken_sec after completion.
    """
    effective_workers: int = int(
        config.get("pipeline", {}).get("n_workers", n_workers)
    )

    log.info(
        "Screen started",
        universe_size=len(universe),
        run_date=str(run_date),
        n_workers=effective_workers,
    )

    t0 = time.monotonic()
    results: list[SEPAResult] = []
    errors = 0

    # ProcessPoolExecutor note: on macOS/Windows, mp_context="spawn" is required
    # because the default "fork" is unsafe. On Linux, fork is used for speed.
    # All arguments to _screen_single must be picklable (no DB connections,
    # no open file handles, no threading.Lock objects).

    # Use spawn context on macOS/Windows to avoid fork-safety issues;
    # fork is safe and faster on Linux.
    if sys.platform == "linux":
        executor_ctx = None  # uses default (fork on Linux)
    else:
        executor_ctx = multiprocessing.get_context("spawn")

    with ProcessPoolExecutor(max_workers=effective_workers, mp_context=executor_ctx) as executor:
        futures = {
            executor.submit(_screen_single, symbol, run_date, config): symbol
            for symbol in universe
        }

        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
                else:
                    errors += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Future raised unexpected exception — symbol skipped",
                    symbol=symbol,
                    error=str(exc),
                )
                errors += 1

    # ── Aggregate stats ───────────────────────────────────────────────────
    passed_stage2      = sum(1 for r in results if r.stage == 2)
    passed_tt          = sum(1 for r in results if r.trend_template_pass)
    vcp_qualified      = sum(1 for r in results if r.vcp_qualified)
    breakout_triggered = sum(1 for r in results if r.breakout_triggered)

    duration = round(time.monotonic() - t0, 2)

    log.info(
        "Screen complete",
        symbols_processed=len(results),
        errors_skipped=errors,
        passed_stage2=passed_stage2,
        passed_tt=passed_tt,
        vcp_qualified=vcp_qualified,
        breakout_triggered=breakout_triggered,
        time_taken_sec=duration,
    )

    # Sort by score descending; ties broken by symbol name (stable sort)
    results.sort(key=lambda r: (-r.score, r.symbol))
    return results
