"""
api/schemas/common.py
─────────────────────
Shared Pydantic models used across every API endpoint.

Conventions (mirror project style from utils/):
  - Python 3.11+  →  use built-in generics and union syntax (X | Y).
  - Pydantic v2   →  model_config, no deprecated validators.
  - No imports from other api/ modules (this file is the foundation layer).
  - Every public symbol is documented so routers can rely on docstrings alone.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Generic type variable — used to parameterise APIResponse
# ─────────────────────────────────────────────────────────────────────────────

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────────────────────
# Universal response envelope
# ─────────────────────────────────────────────────────────────────────────────

class APIResponse(BaseModel, Generic[T]):
    """
    Single envelope returned by every endpoint in the API.

    Fields
    ------
    success : bool
        True when the request was fulfilled normally; False on any error.
    data : T
        The payload — a model, list, or None on error responses.
    meta : dict | None
        Optional metadata: pagination details, run_date, counts, etc.
        Populated by routers; None when not applicable.
    error : str | None
        Human-readable error description.  None when success=True.

    Examples
    --------
    Success with data:
        APIResponse(success=True, data=[...], meta={"total": 3, "date": "2024-01-15"})

    Error with no data:
        APIResponse(success=False, data=None, error="Symbol not found: FOOBAR")
    """

    success: bool
    data: T
    meta: dict | None = None
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Helper constructors
# ─────────────────────────────────────────────────────────────────────────────

def ok(data: T, meta: dict | None = None) -> APIResponse[T]:
    """
    Build a successful APIResponse.

    Parameters
    ----------
    data:
        The response payload.
    meta:
        Optional metadata dict (pagination, run_date, counts, …).

    Returns
    -------
    APIResponse[T] with success=True and error=None.

    Example
    -------
        return ok(results, meta={"date": "2024-01-15", "total": len(results)})
    """
    return APIResponse(success=True, data=data, meta=meta, error=None)


def err(message: str, data: object = None) -> APIResponse[None]:
    """
    Build a failure APIResponse.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    data:
        Optional partial payload to include alongside the error.
        Defaults to None; callers rarely need to populate this.

    Returns
    -------
    APIResponse[None] with success=False and error=message.

    Example
    -------
        return err("Symbol not found: FOOBAR")
    """
    return APIResponse(success=False, data=data, meta=None, error=message)


# ─────────────────────────────────────────────────────────────────────────────
# Pagination metadata
# ─────────────────────────────────────────────────────────────────────────────

class PaginationMeta(BaseModel):
    """
    Standard pagination block placed in APIResponse.meta.

    Routers convert this to a plain dict before passing it to ok():
        meta = PaginationMeta(total=500, limit=20, offset=0, date="2024-01-15")
        return ok(results, meta=meta.model_dump(exclude_none=True))

    Fields
    ------
    total:
        Total number of items matching the query (before limit/offset).
    limit:
        Maximum items returned per page (mirrors the ?limit query param).
    offset:
        Number of items skipped from the start (mirrors ?offset).
    date:
        ISO-8601 date string of the screen run the data belongs to.
        Optional — omit when the response is not date-scoped.
    """

    total: int = Field(..., ge=0, description="Total matching items before pagination.")
    limit: int = Field(..., ge=1, description="Items per page.")
    offset: int = Field(..., ge=0, description="Items skipped from the start.")
    date: str | None = Field(
        default=None,
        description="ISO-8601 screen run date, e.g. '2024-01-15'. None when not applicable.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Request body models
# ─────────────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    """
    Request body for POST /api/v1/run  (admin-only endpoint).

    Triggers a manual pipeline run with configurable scope.

    Fields
    ------
    scope : "all" | "universe" | "watchlist"
        Determines which symbol set is screened:
          "all"        → universe + watchlist (default, mirrors daily run)
          "universe"   → full config/universe.yaml symbols only
          "watchlist"  → SQLite watchlist symbols only, skip universe
    symbols : list[str] | None
        Ad-hoc inline symbol list.  When provided, only these symbols are
        screened regardless of scope.  Useful for quick spot-checks.
        Example: ["RELIANCE", "TCS", "DIXON"]

    Example payloads
    ----------------
        {}                                      → scope="all", symbols=None
        {"scope": "watchlist"}                  → watchlist-only run
        {"symbols": ["RELIANCE", "DIXON"]}      → ad-hoc two-symbol run
    """

    scope: Literal["all", "universe", "watchlist"] = Field(
        default="all",
        description='Symbol scope to screen. One of "all", "universe", "watchlist".',
    )
    symbols: list[str] | None = Field(
        default=None,
        description="Optional inline symbol list. Overrides scope when provided.",
    )


class WatchlistAddNote(BaseModel):
    """
    Optional request body for POST /api/v1/watchlist/{symbol}.

    Allows the caller to attach a free-text note when adding a symbol.

    Fields
    ------
    note : str | None
        Human-readable annotation, e.g. "strong VCP forming — watch for breakout".
        Stored in the watchlist.note column in SQLite.
        Omit (or send null) to add a symbol with no note.

    Example payload
    ---------------
        {"note": "VCP forming, watch entry above 14200"}
    """

    note: str | None = Field(
        default=None,
        description="Optional user note to store with the watchlist entry.",
    )


class BulkAddRequest(BaseModel):
    """
    Request body for POST /api/v1/watchlist/bulk.

    Adds multiple symbols to the watchlist in a single request.

    Fields
    ------
    symbols : list[str]
        Non-empty list of symbol strings to add.
        Each symbol is validated by the router against NSE rules
        (uppercase alphanumeric, 1–20 chars).
        Duplicates are silently skipped (INSERT OR IGNORE semantics).

    Example payload
    ---------------
        {"symbols": ["RELIANCE", "TCS", "DIXON", "TATAELXSI"]}
    """

    symbols: list[str] = Field(
        ...,
        min_length=1,
        description="Non-empty list of NSE symbol strings to add to the watchlist.",
    )
