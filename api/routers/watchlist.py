"""
api/routers/watchlist.py
────────────────────────
Watchlist CRUD endpoints for the Minervini FastAPI layer.

All endpoints are prefixed with /api/v1/watchlist (registered in api/main.py).

Auth tiers:
    require_read_key   — GET /watchlist
    require_admin_key  — all POST and DELETE endpoints

Route ordering note:
    /watchlist/bulk and /watchlist/upload are defined BEFORE /watchlist/{symbol}
    so FastAPI does not treat the literal strings "bulk" and "upload" as path
    parameter values for the {symbol} route.

Supported endpoints:
    GET    /api/v1/watchlist                — list watchlist
    POST   /api/v1/watchlist/bulk           — bulk add symbols
    POST   /api/v1/watchlist/upload         — upload file of symbols
    POST   /api/v1/watchlist/{symbol}       — add single symbol
    DELETE /api/v1/watchlist/{symbol}       — remove single symbol
    DELETE /api/v1/watchlist                — clear entire watchlist
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile

from api.deps import require_admin_key, require_read_key
from api.schemas.common import APIResponse, BulkAddRequest, WatchlistAddNote, err, ok
from api.schemas.stock import WatchlistEntry
from ingestion.universe_loader import load_watchlist_file, validate_symbol
from storage import sqlite_store
from utils.exceptions import SQLiteError, WatchlistParseError
from utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_VALID_SORT = {"score", "symbol", "added_at"}
_SORT_DEFAULT = "score"


def _to_entry(row: dict) -> WatchlistEntry:
    """Convert a raw sqlite_store dict row to a WatchlistEntry Pydantic model."""
    return WatchlistEntry(
        symbol=row["symbol"],
        note=row.get("note"),
        added_at=row["added_at"],
        added_via=row["added_via"],
        last_score=row.get("last_score"),
        last_quality=row.get("last_quality"),
        last_run_at=row.get("last_run_at"),
    )


def _fetch_watchlist(sort_by: str = _SORT_DEFAULT, limit: int = 100) -> list[WatchlistEntry]:
    """Return the current watchlist as WatchlistEntry objects (sorted and limited)."""
    rows = sqlite_store.get_watchlist(sort_by=sort_by)
    return [_to_entry(r) for r in rows[:limit]]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/watchlist
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=APIResponse[list[WatchlistEntry]],
    summary="List watchlist symbols",
    description=(
        "Return all symbols currently in the watchlist, sorted and limited "
        "as requested. Requires a read-tier API key."
    ),
)
def get_watchlist(
    sort: Annotated[
        str,
        Query(description='Sort order: "score" | "symbol" | "added_at". Default: "score".'),
    ] = _SORT_DEFAULT,
    limit: Annotated[
        int,
        Query(ge=1, le=10_000, description="Maximum number of entries to return. Default: 100."),
    ] = 100,
    _key: str = Depends(require_read_key),
) -> APIResponse[list[WatchlistEntry]]:
    """GET /api/v1/watchlist — list current watchlist."""
    if sort not in _VALID_SORT:
        return err(
            f"Invalid sort value '{sort}'. Must be one of: {', '.join(sorted(_VALID_SORT))}."
        )

    try:
        entries = _fetch_watchlist(sort_by=sort, limit=limit)
    except SQLiteError as exc:
        log.error("get_watchlist: DB error", exc_info=True)
        return err(f"Database error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("get_watchlist: unexpected error", exc_info=True)
        return err(f"Unexpected error: {exc}")

    log.info("Watchlist retrieved", count=len(entries), sort=sort, limit=limit)
    return ok(
        entries,
        meta={"total": len(entries), "sort": sort, "limit": limit},
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/watchlist/bulk  — MUST be defined before /{symbol}
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/bulk",
    response_model=APIResponse[dict],
    summary="Bulk-add symbols to the watchlist",
    description=(
        "Validate and add multiple symbols in a single request. "
        "Already-present symbols are silently skipped. "
        "Returns counts of added, already_exists, and invalid symbols. "
        "Requires an admin-tier API key."
    ),
)
def bulk_add(
    body: BulkAddRequest,
    _key: str = Depends(require_admin_key),
) -> APIResponse[dict]:
    """POST /api/v1/watchlist/bulk — add multiple symbols at once."""
    added_list: list[str] = []
    already_exists: list[str] = []
    invalid_list: list[str] = []

    for raw in body.symbols:
        sym = raw.strip().upper()
        if not validate_symbol(sym):
            invalid_list.append(raw)
            log.debug("bulk_add: invalid symbol skipped", symbol=raw)
            continue

        try:
            was_added = sqlite_store.add_symbol(sym, added_via="api")
        except SQLiteError as exc:
            log.error("bulk_add: DB error for symbol", symbol=sym, exc_info=True)
            return err(f"Database error while adding '{sym}': {exc}")
        except Exception as exc:  # noqa: BLE001
            log.error("bulk_add: unexpected error", symbol=sym, exc_info=True)
            return err(f"Unexpected error while adding '{sym}': {exc}")

        if was_added:
            added_list.append(sym)
        else:
            already_exists.append(sym)

    log.info(
        "Watchlist bulk add complete",
        added=len(added_list),
        already_exists=len(already_exists),
        invalid=len(invalid_list),
    )
    return ok(
        {
            "added": added_list,
            "already_exists": already_exists,
            "invalid": invalid_list,
        },
        meta={
            "added_count": len(added_list),
            "already_exists_count": len(already_exists),
            "invalid_count": len(invalid_list),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/watchlist/upload  — MUST be defined before /{symbol}
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_SUFFIXES = {".csv", ".json", ".xlsx", ".txt"}


@router.post(
    "/upload",
    response_model=APIResponse[dict],
    summary="Upload a watchlist file",
    description=(
        "Upload a .csv, .json, .xlsx, or .txt file containing NSE symbols. "
        "Valid symbols are merged into the watchlist (duplicates skipped). "
        "Returns counts and the updated watchlist. "
        "Requires an admin-tier API key."
    ),
)
async def upload_watchlist(
    file: UploadFile,
    _key: str = Depends(require_admin_key),
) -> APIResponse[dict]:
    """POST /api/v1/watchlist/upload — parse an uploaded file and merge symbols."""
    # Validate file extension
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        return err(
            f"Unsupported file type '{suffix}'. "
            f"Accepted: {', '.join(sorted(_ALLOWED_SUFFIXES))}."
        )

    # Write upload to a temp file so load_watchlist_file() can open it
    tmp_path: Path | None = None
    try:
        content = await file.read()

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
            prefix="minervini_wl_",
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        log.debug("Upload saved to temp file", path=str(tmp_path), size_bytes=len(content))

        # Parse file → valid symbol list
        try:
            file_symbols = load_watchlist_file(tmp_path)
        except WatchlistParseError as exc:
            log.warning("upload_watchlist: parse error", reason=str(exc))
            return err(f"Could not parse uploaded file: {exc}")
        except Exception as exc:  # noqa: BLE001
            log.error("upload_watchlist: unexpected parse error", exc_info=True)
            return err(f"Unexpected error parsing file: {exc}")

        # Merge into DB — track per-symbol outcome
        added_list: list[str] = []
        skipped_list: list[str] = []  # already present
        invalid_list: list[str] = []  # failed validate_symbol (shouldn't happen, but defensive)

        for sym in file_symbols:
            if not validate_symbol(sym):
                invalid_list.append(sym)
                continue
            try:
                was_added = sqlite_store.add_symbol(sym, added_via="api")
            except SQLiteError as exc:
                log.error("upload_watchlist: DB error", symbol=sym, exc_info=True)
                return err(f"Database error while adding '{sym}': {exc}")
            except Exception as exc:  # noqa: BLE001
                log.error("upload_watchlist: unexpected DB error", symbol=sym, exc_info=True)
                return err(f"Unexpected error while adding '{sym}': {exc}")

            if was_added:
                added_list.append(sym)
            else:
                skipped_list.append(sym)

        # Return updated watchlist (default sort: score)
        try:
            watchlist_entries = [e.model_dump() for e in _fetch_watchlist()]
        except Exception as exc:  # noqa: BLE001
            log.error("upload_watchlist: failed to fetch updated watchlist", exc_info=True)
            watchlist_entries = []

        log.info(
            "Watchlist upload complete",
            filename=filename,
            added=len(added_list),
            skipped=len(skipped_list),
            invalid=len(invalid_list),
        )
        return ok(
            {
                "added": added_list,
                "skipped": skipped_list,
                "invalid": invalid_list,
                "watchlist": watchlist_entries,
            },
            meta={
                "added_count": len(added_list),
                "skipped_count": len(skipped_list),
                "invalid_count": len(invalid_list),
                "watchlist_total": len(watchlist_entries),
                "filename": filename,
            },
        )

    finally:
        # Always clean up the temp file
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
                log.debug("Temp file cleaned up", path=str(tmp_path))
            except OSError as exc:
                log.warning("Could not delete temp file", path=str(tmp_path), reason=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/watchlist/{symbol}
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{symbol}",
    response_model=APIResponse[list[WatchlistEntry]],
    summary="Add a symbol to the watchlist",
    description=(
        "Validate and add a single NSE symbol to the watchlist. "
        "Returns 422 if the symbol fails format validation. "
        "Returns the full updated watchlist on success. "
        "Requires an admin-tier API key."
    ),
)
def add_symbol(
    symbol: str,
    body: WatchlistAddNote | None = None,
    _key: str = Depends(require_admin_key),
) -> APIResponse[list[WatchlistEntry]]:
    """POST /api/v1/watchlist/{symbol} — add a single symbol."""
    sym = symbol.strip().upper()

    # Validate NSE symbol format
    if not validate_symbol(sym):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid NSE symbol '{sym}'. "
                "Symbols must be 1–20 uppercase letters and/or digits (e.g. RELIANCE, TCS)."
            ),
        )

    note = body.note if body else None

    try:
        was_added = sqlite_store.add_symbol(sym, added_via="api", note=note)
        entries = _fetch_watchlist()
    except SQLiteError as exc:
        log.error("add_symbol: DB error", symbol=sym, exc_info=True)
        return err(f"Database error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("add_symbol: unexpected error", symbol=sym, exc_info=True)
        return err(f"Unexpected error: {exc}")

    log.info("Symbol added via API", symbol=sym, was_new=was_added, note=note)
    return ok(
        entries,
        meta={"symbol": sym, "was_new": was_added, "total": len(entries)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/v1/watchlist/{symbol}
# ─────────────────────────────────────────────────────────────────────────────

@router.delete(
    "/{symbol}",
    response_model=APIResponse[list[WatchlistEntry]],
    summary="Remove a symbol from the watchlist",
    description=(
        "Remove a single symbol from the watchlist. "
        "Returns 404 if the symbol is not present. "
        "Returns the full updated watchlist on success. "
        "Requires an admin-tier API key."
    ),
)
def remove_symbol(
    symbol: str,
    _key: str = Depends(require_admin_key),
) -> APIResponse[list[WatchlistEntry]]:
    """DELETE /api/v1/watchlist/{symbol} — remove a single symbol."""
    sym = symbol.strip().upper()

    try:
        if not sqlite_store.symbol_in_watchlist(sym):
            raise HTTPException(
                status_code=404,
                detail=f"Symbol '{sym}' is not in the watchlist.",
            )

        sqlite_store.remove_symbol(sym)
        entries = _fetch_watchlist()
    except HTTPException:
        raise
    except SQLiteError as exc:
        log.error("remove_symbol: DB error", symbol=sym, exc_info=True)
        return err(f"Database error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("remove_symbol: unexpected error", symbol=sym, exc_info=True)
        return err(f"Unexpected error: {exc}")

    log.info("Symbol removed via API", symbol=sym)
    return ok(
        entries,
        meta={"symbol": sym, "removed": True, "total": len(entries)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/v1/watchlist  — clear entire watchlist
# ─────────────────────────────────────────────────────────────────────────────

@router.delete(
    "",
    response_model=APIResponse[dict],
    summary="Clear the entire watchlist",
    description=(
        "Remove ALL symbols from the watchlist. This action is irreversible. "
        "Returns the number of entries that were cleared. "
        "Requires an admin-tier API key."
    ),
)
def clear_watchlist(
    _key: str = Depends(require_admin_key),
) -> APIResponse[dict]:
    """DELETE /api/v1/watchlist — remove all watchlist symbols."""
    try:
        cleared = sqlite_store.clear_watchlist()
    except SQLiteError as exc:
        log.error("clear_watchlist: DB error", exc_info=True)
        return err(f"Database error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("clear_watchlist: unexpected error", exc_info=True)
        return err(f"Unexpected error: {exc}")

    log.warning("Watchlist cleared via API", cleared=cleared)
    return ok({"cleared": cleared})
