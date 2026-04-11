"""
pipeline/scheduler.py
─────────────────────
APScheduler-based daily pipeline scheduler for the Minervini AI system.

Fires the full screening pipeline on every NSE trading day (Mon–Fri) at
market close (default 15:35 IST), then keeps running so the next day is
covered automatically.

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
    3.  Confirm "Scheduler started, next run: ..." appears in the log.
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
import sys
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
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start_scheduler(
    config: dict,
    db_path: str | Path,
    block: bool = True,
) -> None:
    """
    Start the APScheduler cron scheduler and optionally block until shutdown.

    The scheduler fires _daily_job() every Monday–Friday at the time
    specified by config['scheduler']['run_time'] (default "15:35") in the
    timezone specified by config['scheduler']['timezone']
    (default "Asia/Kolkata").

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

    trigger = CronTrigger(
        day_of_week="mon-fri",
        hour=hour,
        minute=minute,
        timezone=tz,
    )

    scheduler.add_job(
        func=_daily_job,
        trigger=trigger,
        args=[config, db_path],
        id="daily_pipeline",
        name="Minervini daily pipeline",
        replace_existing=True,
        max_instances=1,        # never overlap if a run takes too long
        misfire_grace_time=300, # fire up to 5 min late if the process was busy
    )

    scheduler.start()

    # Determine next run time for the log message
    job = scheduler.get_job("daily_pipeline")
    next_run = job.next_run_time if job else "unknown"
    log.info(
        "Scheduler started, next run: %s",
        next_run,
        run_time=run_time_str,
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
