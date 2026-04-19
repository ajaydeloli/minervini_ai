"""
pipeline/scheduler.py
─────────────────────
APScheduler-based scheduler for the Minervini AI system.

Fires three recurring jobs:
  • Daily pipeline     – every Mon–Fri at market close (default 15:35 IST)
  • Monthly bootstrap  – 1st of each month at 02:00 IST (re-seeds universe)
  • Weekend backtest   – every Saturday at 03:00 IST (opt-in via config)

Public API
──────────
    start_scheduler(config, db_path, block=True) -> None
        Start the background cron scheduler.  When block=True the call
        never returns — use SIGINT / SIGTERM (Ctrl-C) to shut down cleanly.

    run_now(config, db_path, scope="all") -> RunResult
        Execute the pipeline immediately without the scheduler.  Useful for
        CLI one-shots and API-triggered runs.

Usage example
─────────────
    # From a terminal (blocking — the normal production mode):
    python -c "
    import yaml
    from pipeline.scheduler import start_scheduler
    config = yaml.safe_load(open('config/settings.yaml'))
    start_scheduler(config, 'data/minervini.db')
    "

    # Non-blocking (tests / embedding in another process):
    scheduler = start_scheduler(config, db_path, block=False)

    # Manual one-shot:
    from pipeline.scheduler import run_now
    result = run_now(config, 'data/minervini.db', scope='watchlist')
    print(result.status, result.a_plus_count)

Integration smoke-test note
────────────────────────────
Because unit-testing a cron scheduler requires non-trivial time-mocking,
automated tests are not provided for this module.  To verify the wiring
manually:

    1.  Set scheduler.run_time to 2 minutes from now in settings.yaml.
    2.  Run:  python -c "import yaml; from pipeline.scheduler import \\
              start_scheduler; start_scheduler(yaml.safe_load(\\
              open('config/settings.yaml')), 'data/minervini.db')"
    3.  Confirm "Scheduler started" appears in the log with all three
        next-run times.
    4.  Wait for the trigger; verify "Daily job complete" appears.
    5.  Press Ctrl-C; confirm "Scheduler shut down cleanly" appears.

Dependencies
────────────
    APScheduler>=3.10  (already in requirements.txt)
    APScheduler is imported lazily inside function bodies so this module
    can be imported safely even if APScheduler is not installed.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pipeline.context import RunContext
from pipeline.runner import RunResult, run
from utils.date_utils import today_ist
from utils.exceptions import PipelineError
from utils.logger import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_run_time(run_time_str: str) -> tuple[int, int]:
    """
    Parse a "HH:MM" string into (hour, minute) integers.

    Raises:
        PipelineError: If the format is invalid.
    """
    try:
        hour_str, minute_str = run_time_str.strip().split(":")
        return int(hour_str), int(minute_str)
    except (ValueError, AttributeError) as exc:
        raise PipelineError(
            f"Invalid scheduler.run_time '{run_time_str}'. Expected 'HH:MM'.",
            run_time=run_time_str,
        ) from exc


def _build_context(config: dict, db_path: str | Path, scope: str = "all") -> RunContext:
    """Build a fresh RunContext for the current IST date."""
    return RunContext(
        run_date=today_ist(),
        mode="daily",
        scope=scope,          # type: ignore[arg-type]
        config=config,
        db_path=Path(db_path),
        dry_run=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Market-open job (called by APScheduler — must never raise)
# ─────────────────────────────────────────────────────────────────────────────

def _market_open_job(config: dict, db_path: str | Path) -> None:
    """
    Execute all pending paper-trade orders at market open.

    Registered as a Mon–Fri cron job at 09:20 IST (5 min after open so that
    yfinance has a real open price to report for most symbols).  The time is
    configurable via config['scheduler']['market_open_time'] (default '09:20').

    Steps
    ─────
    1. Skip if paper_trading.enabled is False.
    2. Prune orders whose expires_at < today (cancel_expired_orders).
    3. Fetch pending orders; bail early if there are none.
    4. Fetch fill prices via fetch_fill_prices() for all pending symbols.
    5. Call execute_pending_orders() with those prices.

    Swallows ALL exceptions so the scheduler keeps running even if today's
    market-open fill fails.
    """
    if not config.get("paper_trading", {}).get("enabled", False):
        log.debug("Market-open job: paper trading disabled — skipping")
        return

    log.info("Market-open job triggered", run_date=str(today_ist()))
    try:
        from paper_trading.order_queue import (
            cancel_expired_orders,
            execute_pending_orders,
            fetch_fill_prices,
            get_pending_orders,
        )

        db = Path(db_path)

        # Step 1: prune stale orders
        cancelled = cancel_expired_orders(db)
        if cancelled:
            log.info(
                "Market-open job: expired orders cancelled", count=cancelled
            )

        # Step 2: nothing to do?
        pending = get_pending_orders(db)
        if not pending:
            log.info("Market-open job: no pending orders — done")
            return

        symbols = [o.symbol for o in pending]
        log.info(
            "Market-open job: pending orders found",
            count=len(symbols),
            symbols=symbols,
        )

        # Step 3: fetch fill prices (today open or prev-session close)
        current_prices = fetch_fill_prices(symbols, config)
        if not current_prices:
            log.warning(
                "Market-open job: no prices could be fetched — aborting fill",
                symbols=symbols,
            )
            return

        # Step 4: fill orders
        filled = execute_pending_orders(db, current_prices, config)
        log.info(
            "Market-open job: complete",
            filled=len(filled),
            prices_fetched=len(current_prices),
        )

    except Exception as exc:  # noqa: BLE001
        log.critical(
            "Market-open job raised an unhandled exception"
            " — scheduler will retry tomorrow",
            reason=str(exc),
            exc_info=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Daily job (called by APScheduler — must never raise)
# ─────────────────────────────────────────────────────────────────────────────

def _daily_job(config: dict, db_path: str | Path) -> None:
    """
    Entry point executed by the scheduler on each cron tick.

    Builds a RunContext for today (IST), calls pipeline.runner.run(),
    logs a summary, and swallows ALL exceptions so the scheduler keeps
    running for tomorrow even if today's run fails.
    """
    log.info("Daily job triggered", run_date=str(today_ist()))
    try:
        context = _build_context(config, db_path, scope="all")
        result: RunResult = run(context)
        log.info(
            "Daily job complete",
            run_date=str(result.run_date),
            status=result.status,
            symbols_screened=result.symbols_screened,
            passed_stage2=result.passed_stage2,
            passed_tt=result.passed_tt,
            vcp_qualified=result.vcp_qualified,
            a_plus_count=result.a_plus_count,
            a_count=result.a_count,
            alert_sent=result.alert_sent,
            duration_sec=result.duration_sec,
        )
    except Exception as exc:  # noqa: BLE001
        log.critical(
            "Daily job raised an unhandled exception — scheduler will retry tomorrow",
            reason=str(exc),
            exc_info=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Monthly bootstrap job (called by APScheduler — must never raise)
# ─────────────────────────────────────────────────────────────────────────────

def _monthly_bootstrap_job(config: dict, db_path: str | Path) -> None:
    """
    Re-seeds the full universe on the 1st of every month at 02:00 IST.

    Delegates to scripts/bootstrap.py via a subprocess so that the
    long-running import does not block the scheduler thread.  A 2-hour
    timeout prevents it from hanging indefinitely.

    Swallows ALL exceptions so the scheduler keeps running even if the
    bootstrap fails.
    """
    log.info("Monthly bootstrap triggered")
    try:
        result = subprocess.run(
            [sys.executable, "scripts/bootstrap.py", "--universe", "all"],
            check=False,
            timeout=7200,  # 2-hour timeout
        )
        if result.returncode == 0:
            log.info("Monthly bootstrap complete", returncode=result.returncode)
        else:
            log.error(
                "Monthly bootstrap failed",
                returncode=result.returncode,
            )
    except subprocess.TimeoutExpired:
        log.error("Monthly bootstrap failed — subprocess timed out after 2 hours")
    except Exception as exc:  # noqa: BLE001
        log.critical(
            "Monthly bootstrap raised an unhandled exception",
            reason=str(exc),
            exc_info=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Weekend backtest job (called by APScheduler — must never raise)
# ─────────────────────────────────────────────────────────────────────────────

def _weekend_backtest_job(config: dict, db_path: str | Path) -> None:
    """
    Runs a rolling 2-year backtest every Saturday at 03:00 IST.

    Opt-in only: fires only when config['backtest']['weekend_auto_run']
    is explicitly set to True.  Disabled by default so new deployments
    are not surprised by a 3-hour Saturday job.

    Delegates to scripts/backtest_runner.py via subprocess with a 3-hour
    timeout.  Swallows ALL exceptions so the scheduler keeps running.
    """
    if not config.get("backtest", {}).get("weekend_auto_run", False):
        log.debug("Weekend backtest skipped — weekend_auto_run is disabled in config")
        return

    today = date.today()
    end_date_str = today.isoformat()
    start_date_str = (today - timedelta(days=730)).isoformat()  # ~2 years

    log.info(
        "Weekend backtest triggered",
        start=start_date_str,
        end=end_date_str,
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/backtest_runner.py",
                "--start", start_date_str,
                "--end", end_date_str,
            ],
            check=False,
            timeout=10800,  # 3-hour timeout
        )
        if result.returncode == 0:
            log.info(
                "Weekend backtest complete",
                returncode=result.returncode,
                start=start_date_str,
                end=end_date_str,
            )
        else:
            log.error(
                "Weekend backtest failed",
                returncode=result.returncode,
                start=start_date_str,
                end=end_date_str,
            )
    except subprocess.TimeoutExpired:
        log.error("Weekend backtest failed — subprocess timed out after 3 hours")
    except Exception as exc:  # noqa: BLE001
        log.critical(
            "Weekend backtest raised an unhandled exception",
            reason=str(exc),
            exc_info=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start_scheduler(
    config: dict,
    db_path: str | Path,
    block: bool = True,
) -> None:
    """
    Start the APScheduler cron scheduler and optionally block until shutdown.

    Registers three jobs:
      • daily_pipeline    – Mon–Fri at config['scheduler']['run_time'] IST
      • monthly_bootstrap – 1st of each month at 02:00 IST
      • weekend_backtest  – Every Saturday at 03:00 IST (opt-in)

    Args:
        config:   Full application config dict (loaded from settings.yaml).
        db_path:  Path to the SQLite database file.
        block:    If True, block the calling thread and install SIGINT /
                  SIGTERM handlers that call scheduler.shutdown() cleanly.
                  If False, start the background scheduler and return
                  immediately (useful for tests and embedding).

    Raises:
        PipelineError: If scheduler.run_time is malformed.
    """
    # ── Lazy APScheduler import ───────────────────────────────────────────────
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
    except ImportError as exc:
        raise PipelineError(
            "APScheduler is not installed. Run: pip install 'APScheduler>=3.10'",
        ) from exc

    # ── Read scheduler config ─────────────────────────────────────────────────
    sched_cfg: dict = config.get("scheduler", {})
    run_time_str: str = sched_cfg.get("run_time", "15:35")
    tz_name: str = sched_cfg.get("timezone", "Asia/Kolkata")

    hour, minute = _parse_run_time(run_time_str)

    try:
        tz = pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError as exc:
        raise PipelineError(
            f"Unknown timezone '{tz_name}' in scheduler.timezone.",
            timezone=tz_name,
        ) from exc

    # ── Build scheduler ───────────────────────────────────────────────────────
    scheduler = BackgroundScheduler(timezone=tz)

    # -- Job 1: Daily pipeline (Mon–Fri at configured run_time) ---------------
    scheduler.add_job(
        func=_daily_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz),
        args=[config, db_path],
        id="daily_pipeline",
        name="Minervini daily pipeline",
        replace_existing=True,
        max_instances=1,        # never overlap if a run takes too long
        misfire_grace_time=300, # fire up to 5 min late if the process was busy
    )

    # -- Job 2: Monthly bootstrap (1st of each month at 02:00) ----------------
    scheduler.add_job(
        func=_monthly_bootstrap_job,
        trigger=CronTrigger(day=1, hour=2, minute=0, timezone=tz),
        args=[config, db_path],
        id="monthly_bootstrap",
        name="Minervini monthly bootstrap",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # -- Job 3: Weekend backtest (every Saturday at 03:00, opt-in) ------------
    scheduler.add_job(
        func=_weekend_backtest_job,
        trigger=CronTrigger(day_of_week="sat", hour=3, minute=0, timezone=tz),
        args=[config, db_path],
        id="weekend_backtest",
        name="Minervini weekend backtest",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # -- Job 4: Market-open order execution (Mon–Fri, configurable, default 09:20) --
    open_time_str: str = sched_cfg.get("market_open_time", "09:20")
    open_hour, open_minute = _parse_run_time(open_time_str)
    scheduler.add_job(
        func=_market_open_job,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=open_hour,
            minute=open_minute,
            timezone=tz,
        ),
        args=[config, db_path],
        id="market_open_orders",
        name="Minervini market-open pending-order execution",
        replace_existing=True,
        max_instances=1,        # never overlap with itself
        misfire_grace_time=300, # fire up to 5 min late if process was busy
    )

    scheduler.start()

    # ── Determine next run times for the startup log ──────────────────────────
    def _next(job_id: str) -> str:
        job = scheduler.get_job(job_id)
        return str(job.next_run_time) if job else "unknown"

    weekend_auto_run: bool = config.get("backtest", {}).get("weekend_auto_run", False)
    log.info(
        "Scheduler started — four jobs registered",
        daily_pipeline_next=_next("daily_pipeline"),
        market_open_orders_next=_next("market_open_orders"),
        monthly_bootstrap_next=_next("monthly_bootstrap"),
        weekend_backtest_next=_next("weekend_backtest"),
        weekend_backtest_enabled=weekend_auto_run,
        run_time=run_time_str,
        market_open_time=open_time_str,
        timezone=tz_name,
    )

    if not block:
        return  # caller manages lifecycle (tests, embedding)

    # ── Blocking mode: wait for signal ───────────────────────────────────────
    def _shutdown(signum, frame):  # noqa: ANN001
        log.info("Shutdown signal received — stopping scheduler", signal=signum)
        scheduler.shutdown(wait=True)
        log.info("Scheduler shut down cleanly")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Scheduler running (press Ctrl-C to stop)")
    # Keep the main thread alive cheaply
    try:
        signal.pause()
    except AttributeError:
        # signal.pause() is not available on Windows; fall back to a busy-wait
        import time as _time
        while True:
            _time.sleep(60)



def run_now(
    config: dict,
    db_path: str | Path,
    scope: str = "all",
) -> RunResult:
    """
    Execute the pipeline immediately, bypassing the scheduler.

    Intended for:
        - CLI: ``python scripts/run_daily.py --now``
        - REST API: ``POST /api/v1/pipeline/run``
        - Ad-hoc debugging

    Args:
        config:  Full application config dict.
        db_path: Path to the SQLite database file.
        scope:   Symbol scope — "all", "universe", or "watchlist".

    Returns:
        RunResult describing the completed pipeline run.

    Raises:
        Any exception from pipeline.runner.run() propagates to the caller
        (unlike _daily_job, run_now does NOT swallow exceptions).
    """
    log.info("run_now called", scope=scope, db_path=str(db_path))
    context = _build_context(config, db_path, scope=scope)
    result = run(context)
    log.info(
        "run_now complete",
        run_date=str(result.run_date),
        status=result.status,
        a_plus_count=result.a_plus_count,
        a_count=result.a_count,
        duration_sec=result.duration_sec,
    )
    return result
