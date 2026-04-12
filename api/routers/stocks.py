"""
api/routers/stocks.py
─────────────────────
Screener endpoints for Minervini SEPA stock analysis results.

Endpoints (all require X-API-Key / require_read_key)
─────────────────────────────────────────────────────
  GET /api/v1/stocks/top              — top N stocks by score, optional quality filter
  GET /api/v1/stocks/trend            — stocks passing the Minervini Trend Template
  GET /api/v1/stocks/vcp              — VCP-qualified stocks, graded by quality tier
  GET /api/v1/stock/{symbol}          — full StockDetail for one symbol on a date
  GET /api/v1/stock/{symbol}/history  — StockHistory: last N trading days of scores

Design notes
────────────
- All storage access goes through storage.sqlite_store query functions.
  No raw SQL is written here.
- Date params default to date.today().isoformat() when absent.
- Every endpoint body is wrapped in try/except; unexpected exceptions return
  err() with a descriptive message rather than an unhandled 500.
- @limiter.limit(READ_LIMIT) is applied to every endpoint.  The decorated
  function must accept ``request: Request`` as its first non-dep parameter
  so slowapi can read the client IP.
- Incoming requests are logged at DEBUG with key query parameters.
- rr_ratio is not stored as a dedicated DB column; it is parsed from the
  result_json blob in _row_to_detail / _row_to_summary.
"""

from __future__ import annotations

import json
from datetime import date as _date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.requests import Request

from api.deps import require_read_key
from api.rate_limit import READ_LIMIT, limiter
from api.schemas.common import APIResponse, err, ok
from api.schemas.stock import StockDetail, StockHistory, StockSummary
from storage.sqlite_store import get_results_for_date, get_symbol_history
from utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["stocks"])

# ─────────────────────────────────────────────────────────────────────────────
# Quality tier ordering (most → least selective)
# ─────────────────────────────────────────────────────────────────────────────

# Maps a min_quality string to the set of qualifying grades.
# Used by /stocks/vcp where callers expect inclusive-upward filtering.
_VCP_QUALITY_SETS: dict[str, set[str]] = {
    "A+": {"A+"},
    "A":  {"A+", "A"},
    "B":  {"A+", "A", "B"},
    "C":  {"A+", "A", "B", "C"},
}

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _today() -> str:
    """Return today's date as an ISO string (YYYY-MM-DD)."""
    return _date.today().isoformat()


def _coerce_bool(val: Any) -> bool:
    """Convert a SQLite 0/1 integer or Python bool to bool."""
    return bool(val) if val is not None else False


def _coerce_bool_nullable(val: Any) -> bool | None:
    """Convert a SQLite 0/1 integer to bool, preserving None for NULL."""
    if val is None:
        return None
    return bool(val)


def _parse_result_json(row: dict[str, Any]) -> dict[str, Any]:
    """
    Safely parse the result_json blob from a screener_results row.

    Returns an empty dict on missing or malformed JSON so callers can
    safely do `blob.get(...)` without extra null checks.
    """
    raw = row.get("result_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _row_to_summary(row: dict[str, Any]) -> StockSummary:
    """
    Map a screener_results row dict to a StockSummary Pydantic model.

    rr_ratio is not a dedicated screener_results column; it is extracted
    from the result_json blob when available.
    """
    blob = _parse_result_json(row)
    rr_ratio: float | None = blob.get("rr_ratio")

    return StockSummary(
        symbol=row["symbol"],
        score=int(row.get("score") or 0),
        setup_quality=row.get("setup_quality") or "FAIL",
        stage=int(row.get("stage") or 0),
        stage_label=row.get("stage_label") or "",
        rs_rating=int(row.get("rs_rating") or 0),
        trend_template_pass=_coerce_bool(row.get("trend_template_pass")),
        conditions_met=int(row.get("conditions_met") or 0),
        vcp_qualified=_coerce_bool(row.get("vcp_qualified")),
        breakout_triggered=_coerce_bool(row.get("breakout_triggered")),
        entry_price=row.get("entry_price"),
        stop_loss=row.get("stop_loss"),
        risk_pct=row.get("risk_pct"),
        rr_ratio=rr_ratio,
        fundamental_pass=_coerce_bool_nullable(row.get("fundamental_pass")),
        news_score=row.get("news_score"),
        run_date=row["run_date"],
    )


def _row_to_detail(row: dict[str, Any]) -> StockDetail:
    """
    Map a screener_results row dict to a full StockDetail Pydantic model.

    The nested detail dicts (trend_template_details, fundamental_details,
    vcp_details) and the LLM narrative are extracted from result_json.
    All four fields default to None when absent from the blob.
    """
    summary_data = _row_to_summary(row).model_dump()
    blob = _parse_result_json(row)

    return StockDetail(
        **summary_data,
        trend_template_details=blob.get("trend_template_details"),
        fundamental_details=blob.get("fundamental_details"),
        vcp_details=blob.get("vcp_details"),
        narrative=blob.get("narrative"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/stocks/top
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/stocks/top",
    response_model=APIResponse[list[StockSummary]],
    summary="Top stocks by composite SEPA score",
    description=(
        "Returns the highest-scoring stocks for a given screen date, "
        "optionally filtered to an exact setup_quality grade."
    ),
)
@limiter.limit(READ_LIMIT)
def get_top_stocks(
    request: Request,
    quality: str | None = Query(
        default=None,
        description="Exact setup_quality filter: A+, A, B, C, or FAIL. "
                    "Omit to include all grades.",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of results to return (1–100).",
    ),
    date: str | None = Query(
        default=None,
        description="Screen run date as YYYY-MM-DD. Defaults to today.",
    ),
    _key: str = Depends(require_read_key),
) -> APIResponse[list[StockSummary]]:
    run_date = date or _today()
    log.debug("GET /stocks/top", date=run_date, quality=quality, limit=limit)
    try:
        rows = get_results_for_date(run_date, order_by="score DESC")
        if quality:
            rows = [r for r in rows if r.get("setup_quality") == quality]
        rows = rows[:limit]
        results = [_row_to_summary(r) for r in rows]
        meta = {
            "date":  run_date,
            "total": len(results),
            "limit": limit,
        }
        return ok(results, meta=meta)
    except Exception as exc:  # noqa: BLE001
        log.error("GET /stocks/top failed", date=run_date, exc_info=True)
        return err(f"Unexpected error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/stocks/trend
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/stocks/trend",
    response_model=APIResponse[list[StockSummary]],
    summary="Stocks passing the Minervini Trend Template",
    description=(
        "Returns all stocks where all 8 Minervini Trend Template conditions "
        "are satisfied for the given date. Optionally filters by Weinstein "
        "stage and minimum RS rating."
    ),
)
@limiter.limit(READ_LIMIT)
def get_trend_stocks(
    request: Request,
    min_rs: int = Query(
        default=0,
        ge=0,
        le=99,
        description="Minimum RS rating (0–99). Stocks below this are excluded.",
    ),
    stage: int | None = Query(
        default=None,
        description="Weinstein stage (1–4). Omit to include all stages.",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of results to return.",
    ),
    date: str | None = Query(
        default=None,
        description="Screen run date as YYYY-MM-DD. Defaults to today.",
    ),
    _key: str = Depends(require_read_key),
) -> APIResponse[list[StockSummary]]:
    run_date = date or _today()
    log.debug(
        "GET /stocks/trend",
        date=run_date,
        min_rs=min_rs,
        stage=stage,
        limit=limit,
    )
    try:
        rows = get_results_for_date(run_date, order_by="score DESC")

        # Keep only Trend Template passes
        rows = [r for r in rows if _coerce_bool(r.get("trend_template_pass"))]

        # Optional stage filter
        if stage is not None:
            rows = [r for r in rows if r.get("stage") == stage]

        # Minimum RS rating filter
        if min_rs > 0:
            rows = [r for r in rows if int(r.get("rs_rating") or 0) >= min_rs]

        rows = rows[:limit]
        results = [_row_to_summary(r) for r in rows]
        meta = {
            "date":   run_date,
            "total":  len(results),
            "limit":  limit,
            "min_rs": min_rs,
            "stage":  stage,
        }
        return ok(results, meta=meta)
    except Exception as exc:  # noqa: BLE001
        log.error("GET /stocks/trend failed", date=run_date, exc_info=True)
        return err(f"Unexpected error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/stocks/vcp
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/stocks/vcp",
    response_model=APIResponse[list[StockSummary]],
    summary="VCP-qualified stocks",
    description=(
        "Returns stocks where a valid Volatility Contraction Pattern was "
        "detected on the given date. min_quality filters inclusively upward: "
        "'A+' → A+ only; 'A' → A+ and A; 'B' → A+, A, B (default)."
    ),
)
@limiter.limit(READ_LIMIT)
def get_vcp_stocks(
    request: Request,
    min_quality: str = Query(
        default="B",
        description=(
            "Minimum setup quality tier. Inclusive: "
            "'A+' → A+ only; 'A' → A+, A; 'B' → A+, A, B."
        ),
    ),
    limit: int = Query(
        default=30,
        ge=1,
        le=500,
        description="Maximum number of results to return.",
    ),
    date: str | None = Query(
        default=None,
        description="Screen run date as YYYY-MM-DD. Defaults to today.",
    ),
    _key: str = Depends(require_read_key),
) -> APIResponse[list[StockSummary]]:
    run_date = date or _today()
    log.debug(
        "GET /stocks/vcp",
        date=run_date,
        min_quality=min_quality,
        limit=limit,
    )
    try:
        # Resolve the allowed quality grades for the requested tier.
        # Fall back to the full B-and-above set for unrecognised values.
        allowed_grades = _VCP_QUALITY_SETS.get(min_quality, _VCP_QUALITY_SETS["B"])

        rows = get_results_for_date(run_date, order_by="score DESC")

        # Keep only VCP-qualified rows that meet the quality tier
        rows = [
            r for r in rows
            if _coerce_bool(r.get("vcp_qualified"))
            and r.get("setup_quality") in allowed_grades
        ]

        rows = rows[:limit]
        results = [_row_to_summary(r) for r in rows]
        meta = {
            "date":        run_date,
            "total":       len(results),
            "limit":       limit,
            "min_quality": min_quality,
        }
        return ok(results, meta=meta)
    except Exception as exc:  # noqa: BLE001
        log.error("GET /stocks/vcp failed", date=run_date, exc_info=True)
        return err(f"Unexpected error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/stock/{symbol}
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/stock/{symbol}",
    response_model=APIResponse[StockDetail],
    summary="Full SEPA detail for a single symbol",
    description=(
        "Returns the complete StockDetail for the requested symbol on the "
        "given screen date, including nested trend-template, fundamental, "
        "and VCP detail dicts plus any cached LLM narrative. "
        "Returns HTTP 404 when no data exists for the symbol on that date."
    ),
)
@limiter.limit(READ_LIMIT)
def get_stock_detail(
    request: Request,
    symbol: str,
    date: str | None = Query(
        default=None,
        description="Screen run date as YYYY-MM-DD. Defaults to today.",
    ),
    _key: str = Depends(require_read_key),
) -> APIResponse[StockDetail]:
    symbol = symbol.upper().strip()
    run_date = date or _today()
    log.debug("GET /stock/{symbol}", symbol=symbol, date=run_date)
    try:
        rows = get_results_for_date(run_date, order_by="score DESC")

        # Find the row for this specific symbol
        match = next(
            (r for r in rows if r.get("symbol", "").upper() == symbol),
            None,
        )
        if match is None:
            raise HTTPException(
                status_code=404,
                detail=f"No data found for symbol '{symbol}' on {run_date}.",
            )

        detail = _row_to_detail(match)
        meta = {"symbol": symbol, "date": run_date}
        return ok(detail, meta=meta)
    except HTTPException:
        raise  # let FastAPI handle 404 directly
    except Exception as exc:  # noqa: BLE001
        log.error(
            "GET /stock/{symbol} failed",
            symbol=symbol,
            date=run_date,
            exc_info=True,
        )
        return err(f"Unexpected error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/stock/{symbol}/history
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/stock/{symbol}/history",
    response_model=APIResponse[StockHistory],
    summary="Historical SEPA scores for a symbol",
    description=(
        "Returns StockHistory containing up to *days* past screen results for "
        "the requested symbol, most recent first. "
        "The history list is empty when the symbol has never been screened."
    ),
)
@limiter.limit(READ_LIMIT)
def get_stock_history(
    request: Request,
    symbol: str,
    days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Number of past trading days to include (1–365).",
    ),
    _key: str = Depends(require_read_key),
) -> APIResponse[StockHistory]:
    symbol = symbol.upper().strip()
    log.debug("GET /stock/{symbol}/history", symbol=symbol, days=days)
    try:
        rows = get_symbol_history(symbol, days=days)
        summaries = [_row_to_summary(r) for r in rows]
        history = StockHistory(symbol=symbol, history=summaries)
        meta = {
            "symbol": symbol,
            "days":   days,
            "total":  len(summaries),
        }
        return ok(history, meta=meta)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "GET /stock/{symbol}/history failed",
            symbol=symbol,
            days=days,
            exc_info=True,
        )
        return err(f"Unexpected error: {exc}")
