"""
api/routers/health.py
─────────────────────
System health and metadata endpoints.

Endpoints
─────────
  GET /api/v1/health  — public, no auth required
  GET /api/v1/meta    — requires read key (require_read_key)

Design notes
────────────
- Both endpoints are *never-raise*: all exceptions are caught internally
  and returned as graceful payload values (status="error" / null fields).
  This is intentional — monitoring tools must always receive an HTTP 200
  from /health even when the database is unreachable.
- /health queries get_last_run() only; it is cheap and always fast.
- /meta queries several tables and the filesystem (git SHA, config hash)
  in independent try/except blocks so a single failure cannot blank the
  entire response.
- Logging follows the project-wide StructuredLogger pattern:
      log = get_logger(__name__)
      log.info("msg", key=value, ...)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from api.deps import require_read_key
from api.schemas.common import APIResponse, ok
from storage.sqlite_store import get_last_run, get_top_results, get_watchlist
from utils.logger import get_logger
from utils.run_meta import get_config_hash, get_git_sha

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["system"])

# ── Constants ────────────────────────────────────────────────────────────────

_API_VERSION = "1.0.0"
_CONFIG_PATH = Path("config/settings.yaml")

# Run statuses that map to health "degraded"
_DEGRADED_STATUSES = frozenset({"partial", "failed", "running"})


# ── GET /api/v1/health ───────────────────────────────────────────────────────

@router.get(
    "/health",
    response_model=APIResponse[dict],
    summary="System health check",
    description=(
        "Public endpoint — no authentication required. "
        "Returns system status derived from the most recent pipeline run. "
        "Never raises; exceptions surface as status='error'."
    ),
)
def health_check() -> APIResponse[dict]:
    """
    Return system health based on the most recent pipeline run.

    Status mapping
    --------------
    "ok"       — last run completed with status='success'
    "degraded" — last run status is 'partial', 'failed', or 'running'
    "no_data"  — run_history table is empty or DB is missing
    "error"    — unexpected exception reading the database

    The HTTP status code is always 200 so monitoring probes never alert
    solely on transport-level errors.  Consumers should inspect
    data.status to determine actual system health.
    """
    try:
        last_run = get_last_run()

        if last_run is None:
            log.warning("Health check: no run history found in database")
            return ok({
                "status": "no_data",
                "last_run_date": None,
                "last_run_status": None,
                "last_run_duration_sec": None,
                "api_version": _API_VERSION,
            })

        run_status: str | None = last_run.get("status")

        if run_status == "success":
            health_status = "ok"
        elif run_status in _DEGRADED_STATUSES:
            health_status = "degraded"
        else:
            # Unknown / unexpected status value — treat conservatively
            health_status = "degraded"

        log.debug(
            "Health check complete",
            health_status=health_status,
            last_run_date=last_run.get("run_date"),
            last_run_status=run_status,
        )

        return ok({
            "status": health_status,
            "last_run_date": last_run.get("run_date"),
            "last_run_status": run_status,
            "last_run_duration_sec": last_run.get("duration_sec"),
            "api_version": _API_VERSION,
        })

    except Exception:  # noqa: BLE001 — intentional broad catch; health must never raise
        log.error("Health check: unexpected exception", exc_info=True)
        return ok({
            "status": "error",
            "last_run_date": None,
            "last_run_status": None,
            "last_run_duration_sec": None,
            "api_version": _API_VERSION,
        })


# ── GET /api/v1/meta ─────────────────────────────────────────────────────────

@router.get(
    "/meta",
    response_model=APIResponse[dict],
    summary="System metadata summary",
    description=(
        "Returns a snapshot of the system's current state: universe size, "
        "watchlist size, last screen date, A+/A candidate counts, and "
        "code provenance (git SHA + config hash). Requires read key. "
        "Never raises; unavailable fields return null."
    ),
)
def meta(
    _key: str = Depends(require_read_key),
) -> APIResponse[dict]:
    """
    Return system metadata for the dashboard / monitoring layer.

    Data sources
    ------------
    universe_size    : run_history.universe_size for the most recent run
    watchlist_size   : COUNT(*) of the watchlist table
    last_screen_date : run_history.run_date for the most recent run
    a_plus_count     : count of setup_quality='A+' in today's top results
    a_count          : count of setup_quality='A'  in today's top results
    git_sha          : utils.run_meta.get_git_sha()
    config_hash      : utils.run_meta.get_config_hash("config/settings.yaml")

    Each data source is fetched in an independent try/except block so that
    a single failure (e.g. database missing) does not blank the entire
    response.  Callers must tolerate null values for any field.
    """
    data: dict[str, Any] = {
        "universe_size": None,
        "watchlist_size": None,
        "last_screen_date": None,
        "a_plus_count": None,
        "a_count": None,
        "git_sha": None,
        "config_hash": None,
    }

    # ── Last run: universe_size + last_screen_date ─────────────────────────
    try:
        last_run = get_last_run()
        if last_run is not None:
            data["universe_size"] = last_run.get("universe_size")
            data["last_screen_date"] = last_run.get("run_date")
    except Exception:  # noqa: BLE001
        log.warning("Meta: failed to fetch last run row", exc_info=True)

    # ── Watchlist size ─────────────────────────────────────────────────────
    try:
        watchlist = get_watchlist()
        data["watchlist_size"] = len(watchlist)
    except Exception:  # noqa: BLE001
        log.warning("Meta: failed to fetch watchlist count", exc_info=True)

    # ── A+ / A counts from the most recent screen date ─────────────────────
    # Fall back to today's date when no run history is available so the
    # call is still attempted (it will simply return an empty list).
    try:
        screen_date: str = data["last_screen_date"] or str(date.today())
        # Use a large limit so we capture all A+/A rows, not just the top 20.
        top_results = get_top_results(
            run_date=screen_date,
            limit=10_000,
            min_quality="A",
        )
        data["a_plus_count"] = sum(
            1 for r in top_results if r.get("setup_quality") == "A+"
        )
        data["a_count"] = sum(
            1 for r in top_results if r.get("setup_quality") == "A"
        )
    except Exception:  # noqa: BLE001
        log.warning("Meta: failed to fetch top results", exc_info=True)

    # ── Git SHA ────────────────────────────────────────────────────────────
    try:
        data["git_sha"] = get_git_sha()
    except Exception:  # noqa: BLE001
        log.warning("Meta: failed to retrieve git SHA", exc_info=True)

    # ── Config hash ────────────────────────────────────────────────────────
    try:
        data["config_hash"] = get_config_hash(_CONFIG_PATH)
    except Exception:  # noqa: BLE001
        log.warning("Meta: failed to compute config hash", exc_info=True)

    log.info(
        "Meta endpoint served",
        universe_size=data["universe_size"],
        watchlist_size=data["watchlist_size"],
        last_screen_date=data["last_screen_date"],
        a_plus=data["a_plus_count"],
        a=data["a_count"],
    )

    return ok(data)
