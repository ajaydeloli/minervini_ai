"""
storage/sqlite_store.py
───────────────────────
SQLite persistence layer for the Minervini AI system.

Tables managed here:
    watchlist        — user-curated symbols (CLI / API / dashboard)
    run_history      — auditable log of every pipeline run
    screener_results — per-symbol SEPAResult output, one row per symbol per run_date

Design choices:
  - Plain sqlite3 (stdlib) — zero extra dependencies; SQLAlchemy ORM
    is reserved for the API layer if needed later.
  - Single DB file (path configurable, default: data/minervini.db).
  - All public functions open + close their own connection so callers
    never manage connection lifecycle.  A thread-local connection pool
    is deliberately avoided at this stage — the pipeline is single-
    threaded per DB write; the API layer can add its own pool later.
  - Every mutating function uses parameterised queries — no f-string SQL.
  - Schema is created automatically on first use (create_tables).

Usage:
    from storage.sqlite_store import (
        create_tables,
        # watchlist
        add_symbol, remove_symbol, get_watchlist, get_watchlist_symbol,
        bulk_add_symbols, clear_watchlist, update_symbol_score,
        symbol_in_watchlist,
        # run history
        log_run, finish_run, get_last_run, get_run_history,
        # screener results
        save_results, get_results_for_date, get_top_results,
        get_symbol_history,
    )
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Generator, Literal

from utils.exceptions import SQLiteError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Default DB path (overridden at runtime via init_db or env config)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_DB_PATH = Path("data/minervini.db")
_db_path: Path = _DEFAULT_DB_PATH
_lock = threading.Lock()   # serialise writes in case of multi-thread API use


# ─────────────────────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────────────────────

def init_db(path: str | Path = _DEFAULT_DB_PATH) -> None:
    """
    Set the SQLite database path and create all tables if they don't
    exist.  Call once at application startup.

    Args:
        path: Path to the SQLite file.  Parent directories are created
              automatically.  Defaults to data/minervini.db.
    """
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    create_tables()
    log.info("SQLite initialised", db=str(_db_path))


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that yields an open sqlite3 connection and commits
    on clean exit (or rolls back on exception).

    WAL mode is enabled for every connection so readers never block
    writers and writers never block readers — important when the API
    server and the pipeline run concurrently.
    """
    conn = sqlite3.connect(
        str(_db_path),
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        timeout=10,
    )
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")  # safe + faster than FULL
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    return dict(row) if row is not None else None


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- ── Watchlist ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL UNIQUE,
    note         TEXT,
    added_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    added_via    TEXT    NOT NULL CHECK(added_via IN ('cli','api','dashboard','file_upload','test')),
    last_score   REAL,
    last_quality TEXT,
    last_run_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol);

-- ── Run history ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS run_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date       TEXT    NOT NULL,          -- ISO date YYYY-MM-DD
    run_mode       TEXT    NOT NULL CHECK(run_mode IN ('daily','backtest','manual','test')),
    scope          TEXT    NOT NULL DEFAULT 'all',  -- 'all'|'universe'|'watchlist'
    git_sha        TEXT,
    config_hash    TEXT,
    universe_size  INTEGER,
    watchlist_size INTEGER,
    passed_stage2  INTEGER,
    passed_tt      INTEGER,
    vcp_qualified  INTEGER,
    a_plus_count   INTEGER,
    a_count        INTEGER,
    duration_sec   REAL,
    status         TEXT    NOT NULL DEFAULT 'running'
                           CHECK(status IN ('running','success','partial','failed')),
    error_msg      TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    finished_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_history_date ON run_history(run_date);
CREATE INDEX IF NOT EXISTS idx_run_history_status ON run_history(status);

-- ── Screener results ─────────────────────────────────────────────────────────
-- One row per (symbol, run_date).  Mirrors the SEPAResult dataclass fields
-- that are needed for the API and dashboard.  The full JSON blob is also
-- stored so nothing is lost if we add new fields to SEPAResult later.
CREATE TABLE IF NOT EXISTS screener_results (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date              TEXT    NOT NULL,
    symbol                TEXT    NOT NULL,
    score                 REAL,
    setup_quality         TEXT    CHECK(setup_quality IN ('A+','A','B','C','FAIL')),
    stage                 INTEGER,
    stage_label           TEXT,
    stage_confidence      INTEGER,
    trend_template_pass   INTEGER,  -- 0/1 (SQLite has no BOOLEAN)
    conditions_met        INTEGER,
    fundamental_pass      INTEGER,
    vcp_qualified         INTEGER,
    breakout_triggered    INTEGER,
    entry_price           REAL,
    stop_loss             REAL,
    risk_pct              REAL,
    rs_rating             INTEGER,
    news_score            REAL,
    in_watchlist          INTEGER  DEFAULT 0,
    result_json           TEXT,     -- full SEPAResult as JSON blob
    created_at            TEXT     NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE(symbol, run_date)
);

CREATE INDEX IF NOT EXISTS idx_results_run_date   ON screener_results(run_date);
CREATE INDEX IF NOT EXISTS idx_results_symbol      ON screener_results(symbol);
CREATE INDEX IF NOT EXISTS idx_results_quality     ON screener_results(setup_quality);
CREATE INDEX IF NOT EXISTS idx_results_score       ON screener_results(score DESC);
"""


def create_tables(conn: sqlite3.Connection | None = None) -> None:
    """
    Create all tables and indexes if they do not exist.
    Safe to call multiple times (idempotent).

    Args:
        conn: An open connection to use (useful in tests).  If None,
              opens and closes its own connection.
    """
    if conn is not None:
        conn.executescript(_SCHEMA_SQL)
        return

    with _connect() as c:
        c.executescript(_SCHEMA_SQL)


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist — CRUD
# ─────────────────────────────────────────────────────────────────────────────

AddedVia = Literal["cli", "api", "dashboard", "file_upload", "test"]


def add_symbol(
    symbol: str,
    added_via: AddedVia = "cli",
    note: str | None = None,
) -> bool:
    """
    Add a symbol to the watchlist.

    Args:
        symbol:    NSE symbol (will be uppercased).
        added_via: Source of the add operation.
        note:      Optional free-text note.

    Returns:
        True if the symbol was added, False if it already existed.

    Raises:
        SQLiteError: On unexpected database error.
    """
    symbol = symbol.upper().strip()
    try:
        with _lock, _connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO watchlist (symbol, added_via, note)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO NOTHING
                """,
                (symbol, added_via, note),
            )
            added = cursor.rowcount > 0
        if added:
            log.info("Watchlist: symbol added", symbol=symbol, via=added_via)
        else:
            log.debug("Watchlist: symbol already present", symbol=symbol)
        return added
    except sqlite3.Error as exc:
        raise SQLiteError(f"add_symbol({symbol!r}) failed: {exc}") from exc


def remove_symbol(symbol: str) -> bool:
    """
    Remove a symbol from the watchlist.

    Returns:
        True if the symbol was removed, False if it wasn't present.
    """
    symbol = symbol.upper().strip()
    try:
        with _lock, _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM watchlist WHERE symbol = ?", (symbol,)
            )
            removed = cursor.rowcount > 0
        if removed:
            log.info("Watchlist: symbol removed", symbol=symbol)
        return removed
    except sqlite3.Error as exc:
        raise SQLiteError(f"remove_symbol({symbol!r}) failed: {exc}") from exc


def symbol_in_watchlist(symbol: str) -> bool:
    """Return True if *symbol* is in the watchlist."""
    symbol = symbol.upper().strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE symbol = ?", (symbol,)
        ).fetchone()
    return row is not None


def get_watchlist(
    sort_by: Literal["symbol", "score", "added_at"] = "symbol",
) -> list[dict[str, Any]]:
    """
    Return all watchlist symbols as a list of dicts, sorted as requested.

    Args:
        sort_by: Column to sort by.
                 'symbol'   → alphabetical
                 'score'    → highest last_score first (NULLs last)
                 'added_at' → most recently added first

    Returns:
        List of dicts with keys: id, symbol, note, added_at, added_via,
        last_score, last_quality, last_run_at.
    """
    order_clause = {
        "symbol":   "symbol ASC",
        "score":    "last_score DESC NULLS LAST",
        "added_at": "added_at DESC",
    }.get(sort_by, "symbol ASC")

    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM watchlist ORDER BY {order_clause}"  # noqa: S608
        ).fetchall()
    return _rows_to_dicts(rows)


def get_watchlist_symbols(sort_by: Literal["symbol", "score", "added_at"] = "symbol") -> list[str]:
    """Return just the symbol strings from the watchlist."""
    return [r["symbol"] for r in get_watchlist(sort_by=sort_by)]


def get_watchlist_symbol(symbol: str) -> dict[str, Any] | None:
    """Return the watchlist row for *symbol*, or None if absent."""
    symbol = symbol.upper().strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE symbol = ?", (symbol,)
        ).fetchone()
    return _row_to_dict(row)


def bulk_add_symbols(
    symbols: list[str],
    added_via: AddedVia = "cli",
    note: str | None = None,
) -> dict[str, int]:
    """
    Add multiple symbols to the watchlist in a single transaction.
    Symbols already present are skipped silently.

    Args:
        symbols:   List of NSE symbol strings.
        added_via: Source of the bulk-add operation.
        note:      Optional note applied to all new symbols.

    Returns:
        dict with keys 'added' and 'skipped'.
    """
    cleaned = [s.upper().strip() for s in symbols if s.strip()]
    added = skipped = 0
    try:
        with _lock, _connect() as conn:
            for sym in cleaned:
                cursor = conn.execute(
                    """
                    INSERT INTO watchlist (symbol, added_via, note)
                    VALUES (?, ?, ?)
                    ON CONFLICT(symbol) DO NOTHING
                    """,
                    (sym, added_via, note),
                )
                if cursor.rowcount > 0:
                    added += 1
                else:
                    skipped += 1
        log.info("Watchlist: bulk add", added=added, skipped=skipped, via=added_via)
        return {"added": added, "skipped": skipped}
    except sqlite3.Error as exc:
        raise SQLiteError(f"bulk_add_symbols failed: {exc}") from exc


def clear_watchlist() -> int:
    """
    Remove ALL symbols from the watchlist.

    Returns:
        Number of rows deleted.
    """
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM watchlist")
        count = cursor.rowcount
    log.warning("Watchlist cleared", rows_deleted=count)
    return count


def update_symbol_score(
    symbol: str,
    score: float | None,
    quality: str | None,
    run_at: datetime | None = None,
) -> None:
    """
    Update the cached SEPA score fields for a watchlist symbol.
    Called by the screener after each run.

    Args:
        symbol:  NSE symbol.
        score:   Latest composite score (0–100).
        quality: Setup quality tag: 'A+', 'A', 'B', 'C', 'FAIL'.
        run_at:  Timestamp of the run (default: now UTC).
    """
    symbol = symbol.upper().strip()
    ts = (run_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S")
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE watchlist
               SET last_score   = ?,
                   last_quality = ?,
                   last_run_at  = ?
             WHERE symbol = ?
            """,
            (score, quality, ts, symbol),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Run history
# ─────────────────────────────────────────────────────────────────────────────

def log_run(
    run_date: date | str,
    run_mode: Literal["daily", "backtest", "manual", "test"] = "daily",
    scope: str = "all",
    git_sha: str | None = None,
    config_hash: str | None = None,
    universe_size: int | None = None,
    watchlist_size: int | None = None,
) -> int:
    """
    Insert a new run_history row with status='running'.
    Call this at the START of every pipeline run.

    Returns:
        The new row's id (run_id).  Pass this to finish_run() when done.

    Raises:
        SQLiteError: On unexpected database error.
    """
    run_date_str = str(run_date)[:10]
    try:
        with _lock, _connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO run_history
                    (run_date, run_mode, scope, git_sha, config_hash,
                     universe_size, watchlist_size, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
                """,
                (run_date_str, run_mode, scope, git_sha, config_hash,
                 universe_size, watchlist_size),
            )
            run_id = cursor.lastrowid
        log.info("Run started", run_id=run_id, run_date=run_date_str, mode=run_mode)
        return run_id
    except sqlite3.Error as exc:
        raise SQLiteError(f"log_run failed: {exc}") from exc


def finish_run(
    run_id: int,
    status: Literal["success", "partial", "failed"] = "success",
    duration_sec: float | None = None,
    passed_stage2: int | None = None,
    passed_tt: int | None = None,
    vcp_qualified: int | None = None,
    a_plus_count: int | None = None,
    a_count: int | None = None,
    error_msg: str | None = None,
) -> None:
    """
    Update a run_history row with final statistics.
    Call this at the END of every pipeline run (success or failure).

    Args:
        run_id:        The id returned by log_run().
        status:        Final run status.
        duration_sec:  Wall-clock seconds the run took.
        passed_stage2: Count of symbols that passed the Stage 2 gate.
        passed_tt:     Count that passed the Trend Template.
        vcp_qualified: Count with a valid VCP pattern.
        a_plus_count:  A+ setups in this run.
        a_count:       A setups in this run.
        error_msg:     Error message if status != 'success'.
    """
    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with _lock, _connect() as conn:
            conn.execute(
                """
                UPDATE run_history
                   SET status        = ?,
                       duration_sec  = ?,
                       passed_stage2 = ?,
                       passed_tt     = ?,
                       vcp_qualified = ?,
                       a_plus_count  = ?,
                       a_count       = ?,
                       error_msg     = ?,
                       finished_at   = ?
                 WHERE id = ?
                """,
                (status, duration_sec, passed_stage2, passed_tt,
                 vcp_qualified, a_plus_count, a_count, error_msg,
                 finished_at, run_id),
            )
        log.info(
            "Run finished",
            run_id=run_id,
            status=status,
            duration_sec=round(duration_sec or 0, 2),
            a_plus=a_plus_count,
            a=a_count,
        )
    except sqlite3.Error as exc:
        raise SQLiteError(f"finish_run({run_id}) failed: {exc}") from exc


def get_last_run(
    mode: Literal["daily", "backtest", "manual", "test"] | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """
    Return the most recent run_history row (optionally filtered).

    Args:
        mode:   If given, filter to runs of this mode.
        status: If given, filter to runs with this status.

    Returns:
        Dict of the most recent matching row, or None.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if mode:
        clauses.append("run_mode = ?")
        params.append(mode)
    if status:
        clauses.append("status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM run_history {where} ORDER BY id DESC LIMIT 1",  # noqa: S608
            params,
        ).fetchone()
    return _row_to_dict(row)


def get_run_history(
    limit: int = 30,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return the last *limit* run_history rows, most recent first.

    Args:
        limit: Max rows to return.
        mode:  If given, filter to this run mode.
    """
    if mode:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_history WHERE run_mode = ? ORDER BY id DESC LIMIT ?",
                (mode, limit),
            ).fetchall()
    else:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return _rows_to_dicts(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Screener results
# ─────────────────────────────────────────────────────────────────────────────

def save_results(
    results: list[dict[str, Any]],
    run_date: date | str,
    watchlist_symbols: set[str] | None = None,
) -> int:
    """
    Persist a list of SEPAResult dicts to screener_results.

    Each dict must have at minimum: symbol, score, setup_quality.
    All other fields are optional — missing fields default to NULL.

    On conflict (symbol, run_date) the existing row is replaced so
    re-running a screen for the same date is idempotent.

    Args:
        results:           List of SEPAResult dicts (from rules/scorer.py).
        run_date:          The screen date these results belong to.
        watchlist_symbols: Set of symbols in the watchlist (used to set
                           the in_watchlist flag).  Pass None to skip.

    Returns:
        Number of rows written.
    """
    import json

    run_date_str = str(run_date)[:10]
    wl = {s.upper() for s in (watchlist_symbols or set())}

    rows_written = 0
    try:
        with _lock, _connect() as conn:
            for r in results:
                sym = str(r.get("symbol", "")).upper()
                conn.execute(
                    """
                    INSERT INTO screener_results (
                        run_date, symbol, score, setup_quality,
                        stage, stage_label, stage_confidence,
                        trend_template_pass, conditions_met,
                        fundamental_pass, vcp_qualified,
                        breakout_triggered, entry_price, stop_loss,
                        risk_pct, rs_rating, news_score,
                        in_watchlist, result_json
                    ) VALUES (
                        ?,?,?,?,
                        ?,?,?,
                        ?,?,
                        ?,?,
                        ?,?,?,
                        ?,?,?,
                        ?,?
                    )
                    ON CONFLICT(symbol, run_date) DO UPDATE SET
                        score              = excluded.score,
                        setup_quality      = excluded.setup_quality,
                        stage              = excluded.stage,
                        stage_label        = excluded.stage_label,
                        stage_confidence   = excluded.stage_confidence,
                        trend_template_pass= excluded.trend_template_pass,
                        conditions_met     = excluded.conditions_met,
                        fundamental_pass   = excluded.fundamental_pass,
                        vcp_qualified      = excluded.vcp_qualified,
                        breakout_triggered = excluded.breakout_triggered,
                        entry_price        = excluded.entry_price,
                        stop_loss          = excluded.stop_loss,
                        risk_pct           = excluded.risk_pct,
                        rs_rating          = excluded.rs_rating,
                        news_score         = excluded.news_score,
                        in_watchlist       = excluded.in_watchlist,
                        result_json        = excluded.result_json
                    """,
                    (
                        run_date_str,
                        sym,
                        r.get("score"),
                        r.get("setup_quality"),
                        r.get("stage"),
                        r.get("stage_label"),
                        r.get("stage_confidence"),
                        int(bool(r.get("trend_template_pass"))),
                        r.get("conditions_met"),
                        int(bool(r.get("fundamental_pass"))),
                        int(bool(r.get("vcp_qualified"))),
                        int(bool(r.get("breakout_triggered"))),
                        r.get("entry_price"),
                        r.get("stop_loss"),
                        r.get("risk_pct"),
                        r.get("rs_rating"),
                        r.get("news_score"),
                        int(sym in wl),
                        json.dumps(r, default=str),
                    ),
                )
                rows_written += 1

        log.info(
            "Screener results saved",
            run_date=run_date_str,
            rows=rows_written,
        )
        return rows_written
    except sqlite3.Error as exc:
        raise SQLiteError(f"save_results failed for {run_date_str}: {exc}") from exc


def get_results_for_date(
    run_date: date | str,
    min_quality: str | None = None,
    watchlist_only: bool = False,
    order_by: str = "score DESC",
) -> list[dict[str, Any]]:
    """
    Return all screener results for a given run_date.

    Args:
        run_date:       Screen date to query.
        min_quality:    Minimum quality filter. Hierarchy: A+ > A > B > C > FAIL.
                        E.g. 'A' returns A+ and A rows only.
        watchlist_only: If True, return only symbols in the watchlist.
        order_by:       SQL ORDER BY clause (default: score DESC).

    Returns:
        List of result dicts sorted by *order_by*.
    """
    _QUALITY_RANK = {"A+": 4, "A": 3, "B": 2, "C": 1, "FAIL": 0}
    run_date_str = str(run_date)[:10]

    clauses = ["run_date = ?"]
    params: list[Any] = [run_date_str]

    if min_quality and min_quality in _QUALITY_RANK:
        min_rank = _QUALITY_RANK[min_quality]
        qualifying = [q for q, r in _QUALITY_RANK.items() if r >= min_rank]
        placeholders = ",".join("?" * len(qualifying))
        clauses.append(f"setup_quality IN ({placeholders})")
        params.extend(qualifying)

    if watchlist_only:
        clauses.append("in_watchlist = 1")

    where = "WHERE " + " AND ".join(clauses)
    sql = f"SELECT * FROM screener_results {where} ORDER BY {order_by}"  # noqa: S608

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _rows_to_dicts(rows)


def get_top_results(
    run_date: date | str,
    limit: int = 20,
    min_quality: str = "B",
) -> list[dict[str, Any]]:
    """
    Return the top *limit* results for *run_date* by score,
    filtered to at least *min_quality*.

    Watchlist symbols are always sorted first within each quality tier
    (mirrors the report / alert priority from §6.5 of the design doc).
    """
    rows = get_results_for_date(
        run_date,
        min_quality=min_quality,
        order_by="in_watchlist DESC, score DESC",
    )
    return rows[:limit]


def get_symbol_history(
    symbol: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """
    Return the last *days* screener results for a single symbol,
    most recent first.  Used by GET /api/v1/stock/{symbol}/history.

    Args:
        symbol: NSE symbol.
        days:   Maximum number of past run_dates to return.

    Returns:
        List of result dicts ordered by run_date DESC.
    """
    symbol = symbol.upper().strip()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM screener_results
             WHERE symbol = ?
             ORDER BY run_date DESC
             LIMIT ?
            """,
            (symbol, days),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_latest_result(symbol: str) -> dict[str, Any] | None:
    """
    Return the single most recent screener result for *symbol*.
    Returns None if the symbol has never been screened.
    """
    symbol = symbol.upper().strip()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM screener_results
             WHERE symbol = ?
             ORDER BY run_date DESC
             LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    return _row_to_dict(row)


# ─────────────────────────────────────────────────────────────────────────────
# Meta / health helpers (used by GET /api/v1/meta and GET /api/v1/health)
# ─────────────────────────────────────────────────────────────────────────────

def get_meta() -> dict[str, Any]:
    """
    Return a lightweight summary dict suitable for the /api/v1/meta endpoint.

    Returns keys:
        watchlist_size, last_screen_date, a_plus_count, a_count,
        last_run_status, last_run_at.
    """
    with _connect() as conn:
        wl_size = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        last_run = conn.execute(
            """
            SELECT run_date, a_plus_count, a_count, status, finished_at
              FROM run_history
             WHERE status != 'running'
             ORDER BY id DESC LIMIT 1
            """
        ).fetchone()

    if last_run:
        return {
            "watchlist_size":  wl_size,
            "last_screen_date": last_run["run_date"],
            "a_plus_count":     last_run["a_plus_count"],
            "a_count":          last_run["a_count"],
            "last_run_status":  last_run["status"],
            "last_run_at":      last_run["finished_at"],
        }
    return {
        "watchlist_size":  wl_size,
        "last_screen_date": None,
        "a_plus_count":     None,
        "a_count":          None,
        "last_run_status":  None,
        "last_run_at":      None,
    }


def db_path() -> Path:
    """Return the current SQLite database path."""
    return _db_path
