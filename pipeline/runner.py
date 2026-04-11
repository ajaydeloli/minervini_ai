"""
pipeline/runner.py
──────────────────
Main pipeline orchestrator for the Minervini AI daily screening system.

This module replaces the inline pipeline logic that previously lived in
scripts/run_daily.py.  The CLI script remains the entry point for
human-driven runs; runner.py provides a clean, importable API that
schedulers, tests, the backtest harness, and the dashboard can all call.

Public API
──────────
    RunResult   — dataclass summarising one completed pipeline run.
    run(context: RunContext) → RunResult

Orchestration contract
──────────────────────
    • Every numbered step is wrapped in its own try/except.  A single step
      failure logs a WARNING and the pipeline continues — it never aborts
      halfway through.
    • Exception: step 5 (run_screen) failure sets results=[] and marks the
      run status as "partial".
    • dry_run=True skips steps 4, 5, 9, 10, 11 (no feature computation,
      screening, report generation, chart rendering, or alerts).
    • Each step logs INFO at start and end, including elapsed time.
    • Config is loaded externally and injected via RunContext.config.
      Config loading is NOT duplicated here — callers use yaml.safe_load
      or any shared utility before constructing RunContext.
    • No global state; all outputs flow through the returned RunResult.

Notes on step ordering
──────────────────────
The spec lists log_run before resolve_symbols, but log_run requires
universe_size and watchlist_size which only exist after resolve_symbols.
Symbols are therefore resolved first; init_db + log_run follow immediately
after.  The spirit of the spec (audit trail at run start) is preserved.
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pipeline.context import RunContext
from utils.exceptions import TelegramAlertError
from utils.logger import get_logger, setup_logging

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# RunResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    """
    Summary of a completed pipeline run returned by run().

    Attributes
    ──────────
    run_date         : The evaluated trading date.
    symbols_screened : Total symbols that entered run_screen().
    passed_stage2    : Symbols that passed the Stage 2 gate.
    passed_tt        : Symbols that passed the Trend Template.
    vcp_qualified    : Symbols with a confirmed VCP pattern.
    a_plus_count     : Setups graded A+.
    a_count          : Setups graded A.
    duration_sec     : Wall-clock seconds for the entire run.
    csv_path         : Path to the generated watchlist CSV, or None.
    html_path        : Path to the generated watchlist HTML, or None.
    alert_sent       : True if the Telegram alert was dispatched successfully.
    status           : "success" | "partial" | "failed"
    """

    run_date: datetime.date
    symbols_screened: int = 0
    passed_stage2: int = 0
    passed_tt: int = 0
    vcp_qualified: int = 0
    a_plus_count: int = 0
    a_count: int = 0
    duration_sec: float = 0.0
    csv_path: Optional[Path] = None
    html_path: Optional[Path] = None
    alert_sent: bool = False
    status: str = "success"
    paper_trades_entered: int = 0
    paper_trades_queued: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Internal timing helper
# ─────────────────────────────────────────────────────────────────────────────

def _elapsed(t0: float) -> float:
    """Return seconds elapsed since t0, rounded to 3 decimal places."""
    return round(time.monotonic() - t0, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(context: RunContext) -> RunResult:  # noqa: C901
    """
    Execute the full Minervini daily pipeline and return a RunResult.

    Steps
    ─────
    1.  setup_logging()
    2.  resolve_symbols(scope) → RunSymbols
    3.  init_db + log_run()    → run_id
    4.  Feature step: needs_bootstrap / bootstrap / update per symbol  [skip dry_run]
    5.  run_screen()           → list[SEPAResult]                      [skip dry_run]
    6.  persist_results()      — sepa_results table
    7.  save_results()         — screener_results table
    8.  update_symbol_score()  for each watchlist symbol present in results
    9.  generate_watchlist()   → WatchlistOutput                       [skip dry_run]
    10. generate_chart()       for every A+/A result + watchlist gate  [skip dry_run]
    11. TelegramAlert().send() — TelegramAlertError caught → warning   [skip dry_run]
    12. finish_run()
    13. Return RunResult
    """
    wall_start = time.monotonic()
    result     = RunResult(run_date=context.run_date)
    status     = "success"
    error_msg: str | None = None
    run_id: Optional[int] = None

    # ── Step 1: Logging setup ─────────────────────────────────────────────────
    t0 = time.monotonic()
    log.info("Step 1: setup_logging — start")
    try:
        setup_logging()
    except Exception as exc:
        log.warning("Step 1: setup_logging failed — continuing", reason=str(exc))
    log.info("Step 1: setup_logging — done", duration_sec=_elapsed(t0))

    # ── Step 2: Resolve symbols ───────────────────────────────────────────────
    # Done before log_run so universe_size/watchlist_size are available.
    t0 = time.monotonic()
    log.info("Step 2: resolve_symbols — start", scope=context.scope)
    symbols_to_scan:   list[str] = []
    watchlist_symbols: list[str] = []
    universe_size  = 0
    watchlist_size = 0
    try:
        from ingestion.universe_loader import resolve_symbols

        universe_yaml: str = context.config.get(
            "universe_yaml_path", "config/universe.yaml"
        )
        run_symbols = resolve_symbols(
            config_path=universe_yaml,
            cli_watchlist_file=context.cli_watchlist_file,
            cli_symbols=context.cli_symbols,
            scope=context.scope,
        )
        symbols_to_scan   = run_symbols.symbols_to_scan
        watchlist_symbols = run_symbols.watchlist
        universe_size     = len(run_symbols.universe)
        watchlist_size    = len(run_symbols.watchlist)
        log.info(
            "Step 2: resolve_symbols — done",
            watchlist=watchlist_size,
            universe=universe_size,
            to_scan=len(symbols_to_scan),
            duration_sec=_elapsed(t0),
        )
    except Exception as exc:
        log.warning(
            "Step 2: resolve_symbols failed — continuing with empty lists",
            reason=str(exc),
            exc_info=True,
        )
        log.info("Step 2: resolve_symbols — done (error)", duration_sec=_elapsed(t0))

    # ── Step 3: Init DB + log run start ──────────────────────────────────────
    t0 = time.monotonic()
    log.info("Step 3: init_db + log_run — start", db=str(context.db_path))
    try:
        from storage.sqlite_store import init_db, log_run as _log_run

        init_db(context.db_path)  # idempotent; always run so schema is live

        # ── Init paper trading tables if enabled ──────────────────────────
        try:
            if context.config.get("paper_trading", {}).get("enabled", False):
                from paper_trading.portfolio import init_paper_trading_tables
                init_paper_trading_tables(context.db_path)
                log.info("Step 3: paper trading tables initialised")
        except Exception as pt_exc:
            log.warning(
                "Step 3: init_paper_trading_tables failed — continuing",
                reason=str(pt_exc),
                exc_info=True,
            )

        if not context.dry_run:
            from utils.run_meta import get_config_hash, get_git_sha

            run_id = _log_run(
                run_date=context.run_date,
                run_mode=context.mode,
                scope=context.scope,
                git_sha=get_git_sha(),
                config_hash=get_config_hash(context.config_path),
                universe_size=universe_size,
                watchlist_size=watchlist_size,
            )
            log.info(
                "Step 3: init_db + log_run — done",
                run_id=run_id,
                duration_sec=_elapsed(t0),
            )
        else:
            log.info("Step 3: log_run skipped (dry_run)", duration_sec=_elapsed(t0))
    except Exception as exc:
        log.warning(
            "Step 3: init_db/log_run failed — continuing",
            reason=str(exc),
            exc_info=True,
        )
        log.info("Step 3: init_db + log_run — done (error)", duration_sec=_elapsed(t0))

    # ── Step 4: Feature computation ───────────────────────────────────────────
    if not context.dry_run:
        t0 = time.monotonic()
        total = len(symbols_to_scan)
        log.info("Step 4: feature computation — start", symbols=total)
        try:
            from features.feature_store import (
                bootstrap as _bootstrap,
                needs_bootstrap,
                update as _update,
            )
            for idx, symbol in enumerate(symbols_to_scan, start=1):
                try:
                    if needs_bootstrap(symbol, context.config):
                        log.warning(
                            "Feature file missing — bootstrapping",
                            symbol=symbol,
                            progress=f"{idx}/{total}",
                        )
                        _bootstrap(symbol, context.config)
                    else:
                        _update(symbol, context.run_date, context.config)
                except Exception as sym_exc:
                    log.warning(
                        "Feature computation failed — skipping symbol",
                        symbol=symbol,
                        reason=str(sym_exc),
                    )
        except Exception as exc:
            log.warning(
                "Step 4: feature import/setup failed — continuing",
                reason=str(exc),
                exc_info=True,
            )
        log.info("Step 4: feature computation — done", duration_sec=_elapsed(t0))
    else:
        log.info("Step 4: feature computation — skipped (dry_run)")

    # ── Step 5: run_screen ────────────────────────────────────────────────────
    results: list = []
    if not context.dry_run:
        t0 = time.monotonic()
        log.info("Step 5: run_screen — start", symbols=len(symbols_to_scan))
        try:
            from screener.pipeline import run_screen

            results = run_screen(
                universe=symbols_to_scan,
                run_date=context.run_date,
                config=context.config,
            )
            log.info(
                "Step 5: run_screen — done",
                results=len(results),
                duration_sec=_elapsed(t0),
            )
        except Exception as exc:
            log.warning(
                "Step 5: run_screen failed — results=[], status=partial",
                reason=str(exc),
                exc_info=True,
            )
            results = []
            status  = "partial"
            error_msg = f"run_screen failed: {exc}"
            log.info("Step 5: run_screen — done (partial)", duration_sec=_elapsed(t0))
    else:
        log.info("Step 5: run_screen — skipped (dry_run)")

    # ── Step 5b: LLM narrative generation ────────────────────────────────────
    if not context.dry_run and results:
        t0 = time.monotonic()
        log.info("Step 5b: LLM narrative — start", count=len(results))
        try:
            from llm.explainer import generate_trade_brief, generate_watchlist_summary
            import pandas as pd

            only_for = context.config.get("llm", {}).get("only_for_quality", ["A+", "A"])
            features_dir = Path(
                context.config.get("data", {}).get("features_dir", "data/features")
            )
            narrative_count = 0

            for r in results:
                # Always stamp the attribute so downstream code can safely
                # check `getattr(r, "narrative", None)` without AttributeError
                r.narrative = None

                if r.setup_quality not in only_for:
                    continue

                # Load ohlcv_tail from feature parquet
                ohlcv_tail = pd.DataFrame()
                try:
                    fp = features_dir / f"{r.symbol}.parquet"
                    if fp.exists():
                        ohlcv_tail = (
                            pd.read_parquet(fp)
                            .tail(90)[["close", "high", "low", "open", "volume"]]
                        )
                except Exception as load_exc:
                    log.warning(
                        "Step 5b: could not load ohlcv_tail",
                        symbol=r.symbol, reason=str(load_exc),
                    )

                brief = generate_trade_brief(r, ohlcv_tail, context.config)
                r.narrative = brief
                if brief:
                    narrative_count += 1

            # Daily summary narrative (optional — attach to run context or log)
            try:
                summary = generate_watchlist_summary(
                    results, context.run_date, context.config
                )
                if summary:
                    log.info("Step 5b: watchlist summary generated", length=len(summary))
            except Exception as sum_exc:
                log.warning("Step 5b: watchlist summary failed", reason=str(sum_exc))

            log.info(
                "Step 5b: LLM narrative — done",
                narratives_generated=narrative_count,
                duration_sec=_elapsed(t0),
            )
        except Exception as exc:
            log.warning(
                "Step 5b: LLM narrative failed — continuing",
                reason=str(exc),
                exc_info=True,
            )
            log.info("Step 5b: LLM narrative — done (error)", duration_sec=_elapsed(t0))
    else:
        log.info("Step 5b: LLM narrative — skipped (dry_run or no results)")

    # ── Step 6: persist_results (sepa_results table) ──────────────────────────
    t0 = time.monotonic()
    log.info("Step 6: persist_results — start", count=len(results))
    try:
        from screener.results import persist_results

        persist_results(results, context.db_path)
        log.info("Step 6: persist_results — done", duration_sec=_elapsed(t0))
    except Exception as exc:
        log.warning(
            "Step 6: persist_results failed — continuing",
            reason=str(exc),
            exc_info=True,
        )
        log.info("Step 6: persist_results — done (error)", duration_sec=_elapsed(t0))

    # ── Step 7: save_results (screener_results table) ─────────────────────────
    t0 = time.monotonic()
    log.info("Step 7: save_results — start")
    try:
        from storage.sqlite_store import save_results
        from rules.scorer import to_dict

        save_results(
            [to_dict(r) for r in results],
            context.run_date,
            watchlist_symbols=set(watchlist_symbols),
        )
        log.info("Step 7: save_results — done", duration_sec=_elapsed(t0))
    except Exception as exc:
        log.warning(
            "Step 7: save_results failed — continuing",
            reason=str(exc),
            exc_info=True,
        )
        log.info("Step 7: save_results — done (error)", duration_sec=_elapsed(t0))

    # ── Step 8: update_symbol_score for watchlist symbols ─────────────────────
    t0 = time.monotonic()
    log.info("Step 8: update_symbol_score — start", watchlist=len(watchlist_symbols))
    try:
        from storage.sqlite_store import update_symbol_score

        by_symbol = {r.symbol: r for r in results}
        updated = 0
        for sym in watchlist_symbols:
            r = by_symbol.get(sym)
            if r is None:
                continue
            try:
                update_symbol_score(sym, r.score, r.setup_quality)
                updated += 1
            except Exception as sym_exc:
                log.warning(
                    "update_symbol_score failed — skipping symbol",
                    symbol=sym,
                    reason=str(sym_exc),
                )
        log.info(
            "Step 8: update_symbol_score — done",
            updated=updated,
            duration_sec=_elapsed(t0),
        )
    except Exception as exc:
        log.warning(
            "Step 8: update_symbol_score failed — continuing",
            reason=str(exc),
            exc_info=True,
        )
        log.info("Step 8: update_symbol_score — done (error)", duration_sec=_elapsed(t0))

    # ── Step 9: generate_watchlist ────────────────────────────────────────────
    csv_path:  Optional[Path] = None
    html_path: Optional[Path] = None
    if not context.dry_run:
        t0 = time.monotonic()
        log.info("Step 9: generate_watchlist — start", count=len(results))
        if results:
            try:
                from reports.daily_watchlist import generate_watchlist

                wl_out = generate_watchlist(
                    run_date=context.run_date,
                    results=results,
                    config=context.config,
                    watchlist_symbols=set(watchlist_symbols),
                )
                csv_path  = wl_out.csv_path
                html_path = wl_out.html_path
                log.info(
                    "Step 9: generate_watchlist — done",
                    csv=str(csv_path),
                    html=str(html_path),
                    duration_sec=_elapsed(t0),
                )
            except Exception as exc:
                log.warning(
                    "Step 9: generate_watchlist failed — continuing",
                    reason=str(exc),
                    exc_info=True,
                )
                log.info(
                    "Step 9: generate_watchlist — done (error)",
                    duration_sec=_elapsed(t0),
                )
        else:
            log.info(
                "Step 9: generate_watchlist — skipped (no results)",
                duration_sec=_elapsed(t0),
            )
    else:
        log.info("Step 9: generate_watchlist — skipped (dry_run)")

    # ── Step 10: generate_chart ───────────────────────────────────────────────
    if not context.dry_run:
        t0 = time.monotonic()
        log.info("Step 10: generate_chart — start")
        try:
            from reports.chart_generator import generate_chart

            # Always chart every A+ and A result.
            chart_targets: set[str] = {
                r.symbol for r in results if r.setup_quality in ("A+", "A")
            }
            # Gate: also chart every watchlist symbol when enabled in config.
            if context.config.get("watchlist", {}).get("always_generate_charts", False):
                chart_targets |= set(watchlist_symbols)

            by_symbol = {r.symbol: r for r in results}
            charts_ok = 0
            for sym in chart_targets:
                r = by_symbol.get(sym)
                if r is None:
                    continue
                try:
                    generate_chart(
                        symbol=sym,
                        run_date=context.run_date,
                        result=r,
                        config=context.config,
                    )
                    charts_ok += 1
                except Exception as sym_exc:
                    log.warning(
                        "generate_chart failed — skipping symbol",
                        symbol=sym,
                        reason=str(sym_exc),
                    )
            log.info(
                "Step 10: generate_chart — done",
                charts_generated=charts_ok,
                duration_sec=_elapsed(t0),
            )
        except Exception as exc:
            log.warning(
                "Step 10: generate_chart failed — continuing",
                reason=str(exc),
                exc_info=True,
            )
            log.info("Step 10: generate_chart — done (error)", duration_sec=_elapsed(t0))
    else:
        log.info("Step 10: generate_chart — skipped (dry_run)")

    # ── Step 11: Alert dispatch (Telegram + Email + Webhook) ─────────────────
    alert_sent = False
    if not context.dry_run:
        t0 = time.monotonic()
        log.info("Step 11: alert dispatch — start")

        # ── 11a: Telegram ─────────────────────────────────────────────────────
        try:
            from alerts.telegram_alert import TelegramAlert

            TelegramAlert().send(results, context.run_date, context.config)
            alert_sent = True
            log.info("Step 11a: TelegramAlert.send — done")
        except TelegramAlertError as exc:
            log.warning(
                "Step 11a: Telegram alert failed — continuing",
                reason=str(exc),
            )
        except Exception as exc:
            log.warning(
                "Step 11a: TelegramAlert.send unexpected error — continuing",
                reason=str(exc),
                exc_info=True,
            )

        # ── 11b: Email ────────────────────────────────────────────────────────
        try:
            from alerts.email_alert import EmailAlert
            from utils.exceptions import EmailAlertError

            ar = EmailAlert().send(results, context.run_date, context.config)
            if ar.sent > 0:
                alert_sent = True
            log.info("Step 11b: EmailAlert.send — done", sent=ar.sent, skipped=ar.skipped)
        except Exception as exc:
            log.warning(
                "Step 11b: EmailAlert.send failed — continuing",
                reason=str(exc),
                exc_info=True,
            )

        # ── 11c: Webhook ──────────────────────────────────────────────────────
        try:
            from alerts.webhook_alert import WebhookAlert
            from utils.exceptions import WebhookAlertError

            ar = WebhookAlert().send(results, context.run_date, context.config)
            if ar.sent > 0:
                alert_sent = True
            log.info("Step 11c: WebhookAlert.send — done", sent=ar.sent, skipped=ar.skipped)
        except Exception as exc:
            log.warning(
                "Step 11c: WebhookAlert.send failed — continuing",
                reason=str(exc),
                exc_info=True,
            )

        log.info("Step 11: alert dispatch — done", duration_sec=_elapsed(t0))
    else:
        log.info("Step 11: alert dispatch — skipped (dry_run)")

    # ── Step 12: finish_run ───────────────────────────────────────────────────
    # Aggregate final stats — computed here so they go into both finish_run
    # and the returned RunResult without duplicating the list comprehensions.
    passed_stage2 = sum(1 for r in results if r.stage == 2)
    passed_tt     = sum(1 for r in results if r.trend_template_pass)
    vcp_qualified = sum(1 for r in results if r.vcp_qualified)
    a_plus_count  = sum(1 for r in results if r.setup_quality == "A+")
    a_count       = sum(1 for r in results if r.setup_quality == "A")
    total_duration = round(time.monotonic() - wall_start, 2)

    t0 = time.monotonic()
    log.info("Step 12: finish_run — start", run_id=run_id, status=status)
    if run_id is not None:
        try:
            from storage.sqlite_store import finish_run

            finish_run(
                run_id=run_id,
                status=status,
                duration_sec=total_duration,
                passed_stage2=passed_stage2,
                passed_tt=passed_tt,
                vcp_qualified=vcp_qualified,
                a_plus_count=a_plus_count,
                a_count=a_count,
                error_msg=error_msg,
            )
            log.info(
                "Step 12: finish_run — done",
                run_id=run_id,
                status=status,
                duration_sec=_elapsed(t0),
            )
        except Exception as exc:
            log.warning(
                "Step 12: finish_run failed — continuing",
                reason=str(exc),
                exc_info=True,
            )
            log.info("Step 12: finish_run — done (error)", duration_sec=_elapsed(t0))
    else:
        log.info(
            "Step 12: finish_run — skipped (no run_id; dry_run or log_run failed)"
        )

    # ── Step 12b: Paper trading ───────────────────────────────────────────────
    pt_summary: dict = {}
    if not context.dry_run and context.config.get("paper_trading", {}).get("enabled", False):
        t0 = time.monotonic()
        log.info("Step 12b: paper_trading — start", results=len(results))
        try:
            from paper_trading.simulator import process_screen_results
            from paper_trading.report import get_portfolio_summary, format_summary_text
            pt_summary = process_screen_results(results, context.db_path, context.config)
            log.info(
                "Step 12b: paper_trading — trades processed",
                entered=pt_summary["entered"],
                pyramided=pt_summary["pyramided"],
                queued=pt_summary["queued"],
                skipped=pt_summary["skipped"],
            )
            # Log portfolio summary after processing
            port_summary = get_portfolio_summary(context.db_path)
            log.info(
                "Step 12b: portfolio state",
                total_value=port_summary.total_value,
                cash=port_summary.cash,
                open_trades=port_summary.open_trades,
                win_rate=port_summary.win_rate,
            )
            # Print human-readable summary to stdout (visible in terminal runs)
            print(format_summary_text(port_summary))
        except Exception as exc:
            log.warning(
                "Step 12b: paper_trading failed — continuing",
                reason=str(exc),
                exc_info=True,
            )
        log.info("Step 12b: paper_trading — done", duration_sec=_elapsed(t0))
    else:
        log.info("Step 12b: paper_trading — skipped (dry_run or disabled)")

    # ── Step 13: Build and return RunResult ───────────────────────────────────
    result.symbols_screened = 0 if context.dry_run else len(symbols_to_scan)
    result.passed_stage2    = passed_stage2
    result.passed_tt        = passed_tt
    result.vcp_qualified    = vcp_qualified
    result.a_plus_count     = a_plus_count
    result.a_count          = a_count
    result.duration_sec     = total_duration
    result.csv_path         = csv_path
    result.html_path        = html_path
    result.alert_sent       = alert_sent
    result.status           = status
    result.paper_trades_entered = pt_summary.get("entered", 0) if pt_summary else 0
    result.paper_trades_queued  = pt_summary.get("queued",  0) if pt_summary else 0

    log.info(
        "Pipeline run complete",
        run_date=str(context.run_date),
        mode=context.mode,
        scope=context.scope,
        symbols_screened=result.symbols_screened,
        passed_stage2=result.passed_stage2,
        a_plus=result.a_plus_count,
        a=result.a_count,
        status=result.status,
        duration_sec=result.duration_sec,
    )

    return result
