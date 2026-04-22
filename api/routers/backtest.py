"""
api/routers/backtest.py
───────────────────────
Read-only backtest history endpoints.

The backtest engine writes two artefacts per run:
  • A row in the run_history SQLite table  (run_mode='backtest')
  • reports/backtest/metrics_{run_label}_{date}.json  (full stats / metrics blob)
  • data/backtests/{run_id}/equity_curve.csv          (optional daily curve)

Endpoints (all require X-API-Key / require_read_key, READ-ONLY)
───────────────────────────────────────────────────────────────
  GET /api/v1/backtest/runs
      List every run_history row where run_mode='backtest'.
      Returned fields: run_id, run_date, status, duration_sec,
      a_plus_count, a_count.

  GET /api/v1/backtest/runs/{run_id}/summary
      Search reports/backtest/ for metrics_*.json files and return the best
      match for the given run_id (or the newest file when no match exists).
      Returns HTTP 404 when the directory contains no metrics files.

  GET /api/v1/backtest/equity-curve
      Query param: run_id (str | None, defaults to most-recent backtest).
      Read data/backtests/{run_id}/equity_curve.csv.
      Columns surfaced: date, portfolio_value, benchmark_value, regime.
      Returns HTTP 404 when the file does not exist.

Design rules  (mirror api/routers/portfolio.py)
───────────────────────────────────────────────
  • READ-ONLY — no write functions are ever called.
  • Every endpoint requires X-API-Key authentication (require_read_key).
  • All file I/O is wrapped in try/except; missing files raise HTTP 404.
  • pathlib.Path is used for all file operations.
  • Unexpected exceptions return err() — never an unhandled 500.
  • @limiter.limit(READ_LIMIT) is applied to every endpoint.
  • `request: Request` is the first non-dep param on all rate-limited fns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.requests import Request

from api.deps import require_read_key
from api.rate_limit import READ_LIMIT, limiter
from api.schemas.common import APIResponse, err, ok
from storage.sqlite_store import get_run_history, get_run_by_id
from utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/backtest", tags=["backtest"])

# ─────────────────────────────────────────────────────────────────────────────
# File-system path constants  (CWD-relative, mirrors the rest of the project)
# ─────────────────────────────────────────────────────────────────────────────

_REPORTS_DIR: Path = Path("reports/backtest")
_BACKTESTS_DIR: Path = Path("reports/backtest")

# Columns the equity-curve endpoint will surface (in this order, if present)
_EQUITY_CURVE_COLS = ["date", "portfolio_value", "benchmark_value", "regime"]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _project_run_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Project a raw run_history dict to the subset of fields exposed by the
    /backtest/runs endpoint.

    The DB primary key column is named 'id'; we surface it as 'run_id' in
    the API response so callers can pass it directly to the /summary and
    /equity-curve endpoints.
    """
    return {
        "run_id":       row.get("id"),
        "run_date":     row.get("run_date"),
        "status":       row.get("status"),
        "duration_sec": row.get("duration_sec"),
        "a_plus_count": row.get("a_plus_count"),
        "a_count":      row.get("a_count"),
    }


def _get_report_path_from_db(run_id: str) -> Path:
    """
    Look up the ``report_path`` column stored by backtest_runner for the
    given integer run_id.

    Args:
        run_id: The string representation of the run_history primary key.

    Returns:
        A ``pathlib.Path`` pointing to the metrics JSON file.

    Raises:
        FileNotFoundError: when the run does not exist in the DB, the
            ``report_path`` column is NULL (run pre-dates this feature), or
            the stored path no longer exists on disk.
        ValueError: when *run_id* cannot be converted to an integer.
    """
    try:
        int_id = int(run_id)
    except (TypeError, ValueError):
        raise ValueError(f"run_id must be an integer string, got {run_id!r}")

    row = get_run_by_id(int_id)
    if row is None:
        raise FileNotFoundError(
            f"No run_history row found for run_id '{run_id}'."
        )

    stored_path: str | None = row.get("report_path")
    if not stored_path:
        raise FileNotFoundError(
            f"run_id '{run_id}' has no report_path recorded "
            f"(run may pre-date this feature)."
        )

    path = Path(stored_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Metrics file for run_id '{run_id}' not found on disk: {path}"
        )

    log.debug("_get_report_path_from_db: resolved", run_id=run_id, path=str(path))
    return path


def _read_report_file(path: Path) -> dict[str, Any]:
    """
    JSON-parse a metrics file returned by ``_get_report_path_from_db``.

    Raises:
        json.JSONDecodeError: when the file is not valid JSON.
    """
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/backtest/runs
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/runs",
    response_model=APIResponse[list[dict]],
    summary="List backtest run history",
    description=(
        "Returns a summary list of every pipeline run recorded with "
        "run_mode='backtest', most recent first. "
        "Each entry exposes: run_id, run_date, status, duration_sec, "
        "a_plus_count, a_count. "
        "Returns an empty list when no backtest runs have been recorded yet."
    ),
)
@limiter.limit(READ_LIMIT)
def list_backtest_runs(
    request: Request,
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of runs to return (1–500), most recent first.",
    ),
    _key: str = Depends(require_read_key),
) -> APIResponse[list[dict]]:
    log.debug("GET /backtest/runs", limit=limit)
    try:
        rows = get_run_history(mode="backtest", limit=limit)
        summaries = [_project_run_row(r) for r in rows]
        return ok(summaries, meta={"total": len(summaries), "limit": limit})
    except Exception as exc:  # noqa: BLE001
        log.error("GET /backtest/runs failed", exc_info=True)
        return err(f"Unexpected error fetching backtest run history: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/backtest/runs/{run_id}/summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/runs/{run_id}/summary",
    response_model=APIResponse[dict],
    summary="Full backtest report for a single run",
    description=(
        "Searches reports/backtest/ for metrics_*.json files and returns the "
        "best match for the given run_id (or the newest file when no exact "
        "match exists), wrapped in APIResponse[dict]. "
        "Returns HTTP 404 when no metrics files are found in the directory."
    ),
)
@limiter.limit(READ_LIMIT)
def get_backtest_summary(
    request: Request,
    run_id: str,
    _key: str = Depends(require_read_key),
) -> APIResponse[dict]:
    log.debug("GET /backtest/runs/{run_id}/summary", run_id=run_id)
    try:
        path = _get_report_path_from_db(run_id)
        report = _read_report_file(path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"No backtest report found for run_id '{run_id}'.",
        )
    except json.JSONDecodeError as exc:
        log.error(
            "GET /backtest/runs/{run_id}/summary — malformed JSON",
            run_id=run_id,
            exc_info=True,
        )
        return err(f"Backtest report for run_id '{run_id}' contains malformed JSON: {exc}")
    except OSError as exc:
        log.error(
            "GET /backtest/runs/{run_id}/summary — I/O error",
            run_id=run_id,
            exc_info=True,
        )
        return err(f"Could not read backtest report for run_id '{run_id}': {exc}")

    return ok(report, meta={"run_id": run_id})


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/backtest/equity-curve
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/equity-curve",
    response_model=APIResponse[list[dict]],
    summary="Daily equity curve for a backtest run",
    description=(
        "Returns the daily equity curve from "
        "data/backtests/{run_id}/equity_curve.csv. "
        "When run_id is omitted the most recent backtest run is used. "
        "Surfaced columns: date, portfolio_value, benchmark_value, regime "
        "(null-filled when a column is absent from the CSV). "
        "Returns HTTP 404 when no equity_curve.csv file is found."
    ),
)
@limiter.limit(READ_LIMIT)
def get_equity_curve(
    request: Request,
    run_id: str | None = Query(
        default=None,
        description=(
            "Backtest run_id whose equity curve to fetch. "
            "Omit to use the most recent backtest run."
        ),
    ),
    _key: str = Depends(require_read_key),
) -> APIResponse[list[dict]]:
    import pandas as pd

    log.debug("GET /backtest/equity-curve", run_id=run_id)

    # ── Resolve run_id (default to most-recent backtest) ──────────────────
    if run_id is None:
        try:
            recent = get_run_history(mode="backtest", limit=1)
        except Exception as exc:  # noqa: BLE001
            log.error("Equity-curve: failed to resolve most-recent run", exc_info=True)
            return err(f"Could not determine most recent backtest run: {exc}")

        if not recent:
            raise HTTPException(
                status_code=404,
                detail="No backtest runs found; cannot resolve a default run_id.",
            )
        run_id = str(recent[0]["id"])
        log.debug("Equity-curve: resolved run_id from DB", run_id=run_id)

    # ── Locate the equity-curve CSV via glob (newest file wins) ──────────
    #
    # report.py writes:  reports/backtest/equity_curve_{label}_{date}.csv
    # When run_id is supplied we prefer files whose name contains that
    # run_id token; otherwise we fall back to the newest file overall.
    candidates = sorted(
        _BACKTESTS_DIR.glob("equity_curve_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="No equity_curve_*.csv files found in reports/backtest/.",
        )

    # Prefer a file that contains the run_id in its stem (best-effort match)
    csv_path = candidates[0]          # default: most-recently modified
    if run_id is not None:
        for c in candidates:
            if run_id in c.stem:
                csv_path = c
                break

    log.debug("Equity-curve: using CSV", path=str(csv_path), run_id=run_id)

    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"No equity_curve.csv found for run_id '{run_id}'.",
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "GET /backtest/equity-curve — CSV read failed",
            run_id=run_id,
            path=str(csv_path),
            exc_info=True,
        )
        return err(f"Could not read equity curve for run_id '{run_id}': {exc}")

    # ── Project to the documented columns (missing ones → None per row) ───
    present = [c for c in _EQUITY_CURVE_COLS if c in df.columns]
    missing = [c for c in _EQUITY_CURVE_COLS if c not in df.columns]
    if missing:
        log.debug(
            "Equity-curve: columns absent from CSV — will be null",
            missing=missing,
            run_id=run_id,
        )
        for col in missing:
            df[col] = None

    records: list[dict] = df[_EQUITY_CURVE_COLS].to_dict(orient="records")

    return ok(
        records,
        meta={"run_id": run_id, "rows": len(records)},
    )
