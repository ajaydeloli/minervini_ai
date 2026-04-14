"""
api/routers/run.py
──────────────────
Admin-only manual pipeline trigger endpoint.

Endpoint
────────
  POST /run  (mounted under /api/v1 in api/main.py → POST /api/v1/run)

Design notes
────────────
- Requires admin key via require_admin_key (from api/deps.py).
- Rate-limited with ADMIN_LIMIT ("10/minute") via slowapi.
- A threading.Event (_run_in_progress) prevents concurrent pipeline runs.
  The flag is SET atomically in trigger_run() before the background task is
  spawned, and CLEARED in the finally-block of _run_pipeline_in_background().
- Heavy pipeline imports (pipeline.context, pipeline.runner) are deferred
  inside the background function to avoid circular-import issues at load time.
- Prometheus gauges (last_run_duration_sec, last_run_a_plus_count) are owned
  here and exported from this module so api/main.py no longer needs them.
"""

from __future__ import annotations

import asyncio
import datetime
import threading
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from prometheus_client import Gauge
from starlette.requests import Request

from api.auth import require_admin_key
from api.deps import get_config, get_db_path
from api.rate_limit import ADMIN_LIMIT, limiter
from api.schemas.common import APIResponse, RunRequest
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="", tags=["Admin"])

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus gauges — owned here, removed from api/main.py
# ─────────────────────────────────────────────────────────────────────────────

last_run_duration_sec = Gauge(
    "minervini_last_run_duration_sec",
    "Last pipeline run duration in seconds",
)

last_run_a_plus_count = Gauge(
    "minervini_last_run_a_plus_count",
    "Last run A+ setup count",
)

# ─────────────────────────────────────────────────────────────────────────────
# Run-in-progress guard
#
# SET synchronously in trigger_run() BEFORE spawning the background task so
# any concurrent /run request sees the flag and receives 409 immediately.
# CLEARED in the finally-block of _run_pipeline_in_background() regardless
# of pipeline outcome.
# ─────────────────────────────────────────────────────────────────────────────

_run_in_progress = threading.Event()

# ─────────────────────────────────────────────────────────────────────────────
# Background worker
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline_in_background(
    scope: str,
    symbols: list[str] | None,
    db_path: Path,
    config: dict,
) -> None:
    """
    Execute the Minervini pipeline in a background thread.

    Pipeline imports are deferred to avoid circular imports at module load
    time and to ensure they are only resolved when an actual /run request
    is made.

    _run_in_progress is set by the caller (trigger_run) BEFORE this function
    is scheduled, so a second /run request arriving before the thread starts
    will still see the flag and receive a 409.  This function is responsible
    for clearing the flag in its finally block regardless of outcome.
    """
    from pipeline.context import RunContext
    from pipeline import runner  # deferred — heavy import

    try:
        context = RunContext(
            run_date=datetime.date.today(),
            mode="manual",
            scope=scope,        # type: ignore[arg-type]
            config=config,
            db_path=db_path,
        )
        if symbols:
            context.cli_symbols = symbols

        result = runner.run(context)

        # Mirror key metrics into Prometheus gauges
        last_run_duration_sec.set(result.duration_sec)
        last_run_a_plus_count.set(result.a_plus_count)

        log.info(
            "Manual pipeline run complete",
            scope=scope,
            status=result.status,
            duration_sec=result.duration_sec,
            a_plus=result.a_plus_count,
        )
    except Exception:
        log.error("Background pipeline run failed", exc_info=True)
    finally:
        _run_in_progress.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    response_model=APIResponse[dict],
    status_code=202,
    summary="Trigger a manual pipeline run",
    description=(
        "Admin-only. Queues a background pipeline run and returns 202 "
        "immediately. Returns 409 if a run is already in progress."
    ),
)
@limiter.limit(ADMIN_LIMIT)
async def trigger_run(
    request: Request,
    body: RunRequest,
    _key: str = Depends(require_admin_key),
) -> JSONResponse:
    """POST /api/v1/run — trigger a manual pipeline run (non-blocking)."""

    if _run_in_progress.is_set():
        conflict_body = APIResponse(
            success=False,
            data=None,
            error="Run already in progress",
        ).model_dump()
        return JSONResponse(status_code=409, content=conflict_body)

    # Set the flag NOW (before spawning) to close the race window.
    _run_in_progress.set()

    db_path: Path = get_db_path()
    config: dict = get_config()
    scope: str = body.scope
    symbols: list[str] | None = body.symbols

    # asyncio.to_thread runs the blocking pipeline in a thread-pool thread
    # without blocking the event loop; create_task ensures the response is
    # sent before the pipeline starts executing.
    asyncio.create_task(
        asyncio.to_thread(
            _run_pipeline_in_background,
            scope,
            symbols,
            db_path,
            config,
        )
    )

    log.info(
        "Pipeline run queued",
        scope=scope,
        symbols=symbols,
    )

    accepted_body = APIResponse(
        success=True,
        data={"status": "queued", "scope": scope},
    ).model_dump()

    return JSONResponse(status_code=202, content=accepted_body)
