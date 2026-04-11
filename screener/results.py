"""
screener/results.py
───────────────────
SQLite persistence and query helpers for SEPAResult objects produced by
screener/pipeline.py.

This module owns the ``sepa_results`` table — a lean, deduplicated store
that the API layer queries to serve the /screen endpoints.  It is separate
from storage/sqlite_store.py (which owns watchlist, run_history, and the
richer screener_results table) so each module stays focused on a single
responsibility.

Public API
──────────
    create_table(db_path)                                     → None
    persist_results(results, db_path)                         → None
    load_results(db_path, run_date=None, min_quality=None)    → list[dict]

Design
──────
    - Plain sqlite3 (stdlib) — no SQLAlchemy.
    - Idempotent table creation (CREATE TABLE IF NOT EXISTS).
    - persist_results skips duplicates on (symbol, date) via INSERT OR IGNORE.
    - load_results returns a list of plain dicts (JSON-serialisable).
    - WAL mode on every connection for concurrency safety.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from rules.scorer import SEPAResult
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sepa_results (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT    NOT NULL,
    date                DATE    NOT NULL,
    stage               INT,
    stage_label         TEXT,
    trend_template_pass BOOLEAN,
    conditions_met      INT,
    vcp_qualified       BOOLEAN,
    vcp_grade           TEXT,
    breakout_triggered  BOOLEAN,
    entry_price         REAL,
    stop_loss           REAL,
    risk_pct            REAL,
    rr_ratio            REAL,
    target_price        REAL,
    reward_pct          REAL,
    has_resistance      BOOLEAN,
    rs_rating           INT,
    setup_quality       TEXT,
    score               REAL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_sepa_results_date    ON sepa_results(date);
CREATE INDEX IF NOT EXISTS idx_sepa_results_symbol  ON sepa_results(symbol);
CREATE INDEX IF NOT EXISTS idx_sepa_results_quality ON sepa_results(setup_quality);
CREATE INDEX IF NOT EXISTS idx_sepa_results_score   ON sepa_results(score DESC);
"""

# ── Migration: add R:R columns to databases created before Phase 4 ────────────
# ALTER TABLE ... ADD COLUMN is a no-op in SQLite when the column already exists
# via IF NOT EXISTS (SQLite 3.37+).  For older SQLite we catch the OperationalError.
_MIGRATE_SQL = [
    "ALTER TABLE sepa_results ADD COLUMN rr_ratio      REAL",
    "ALTER TABLE sepa_results ADD COLUMN target_price  REAL",
    "ALTER TABLE sepa_results ADD COLUMN reward_pct    REAL",
    "ALTER TABLE sepa_results ADD COLUMN has_resistance BOOLEAN",
]

# ─────────────────────────────────────────────────────────────────────────────
# Quality ranking (A+ > A > B > C > FAIL)
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_RANK: dict[str, int] = {"A+": 4, "A": 3, "B": 2, "C": 1, "FAIL": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _connect(db_path: str | Path) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager yielding an open WAL-mode connection.
    Commits on clean exit; rolls back on exception.
    """
    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        timeout=10,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_parent(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def create_table(db_path: str | Path) -> None:
    """
    Create the ``sepa_results`` table and its indexes if they do not exist.
    Safe to call multiple times (idempotent).

    Parameters
    ──────────
    db_path : Path to the SQLite database file.  Parent dirs are created.
    """
    db_path = Path(db_path)
    _ensure_parent(db_path)
    with _connect(db_path) as conn:
        conn.executescript(_CREATE_TABLE_SQL)
        # Migration guard: add R:R columns to pre-Phase-4 databases.
        for _sql in _MIGRATE_SQL:
            try:
                conn.execute(_sql)
            except sqlite3.OperationalError:
                pass   # column already exists — safe to ignore
    log.debug("sepa_results table ensured", db=str(db_path))


def persist_results(
    results: list[SEPAResult],
    db_path: str | Path,
) -> None:
    """
    Insert SEPAResult objects into the ``sepa_results`` table.

    Duplicates on (symbol, date) are silently skipped via INSERT OR IGNORE —
    re-running a screen for the same date is idempotent.

    The table is created automatically if it does not exist.

    Parameters
    ──────────
    results : list[SEPAResult] from screener/pipeline.py.
    db_path : Path to the SQLite database file.
    """
    db_path = Path(db_path)
    create_table(db_path)

    rows_inserted = 0
    rows_skipped = 0

    with _connect(db_path) as conn:
        for r in results:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO sepa_results (
                    symbol, date,
                    stage, stage_label,
                    trend_template_pass, conditions_met,
                    vcp_qualified, vcp_grade,
                    breakout_triggered,
                    entry_price, stop_loss, risk_pct,
                    rr_ratio, target_price, reward_pct, has_resistance,
                    rs_rating, setup_quality, score
                ) VALUES (
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    r.symbol,
                    r.date.isoformat(),
                    r.stage,
                    r.stage_label,
                    int(r.trend_template_pass),
                    r.conditions_met,
                    int(r.vcp_qualified),
                    r.vcp_grade,
                    int(r.breakout_triggered),
                    r.entry_price,
                    r.stop_loss,
                    r.risk_pct,
                    r.rr_ratio,
                    r.target_price,
                    r.reward_pct,
                    int(r.has_resistance) if r.has_resistance is not None else None,
                    r.rs_rating,
                    r.setup_quality,
                    r.score,
                ),
            )
            if cursor.rowcount > 0:
                rows_inserted += 1
            else:
                rows_skipped += 1

    log.info(
        "persist_results complete",
        db=str(db_path),
        inserted=rows_inserted,
        skipped_duplicates=rows_skipped,
        total=len(results),
    )


def load_results(
    db_path: str | Path,
    run_date: str | None = None,
    min_quality: str | None = None,
) -> list[dict]:
    """
    Query sepa_results and return matching rows as plain dicts.

    Parameters
    ──────────
    db_path     : Path to the SQLite database file.
    run_date    : ISO date string "YYYY-MM-DD".  When given, only rows for
                  that date are returned.  None returns all dates.
    min_quality : Minimum quality filter ("A+", "A", "B", "C", "FAIL").
                  E.g. "B" returns A+, A, and B rows.  None returns all.

    Returns
    ───────
    list[dict] — one dict per row, sorted by score descending.
    Returns an empty list when the table does not exist.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        log.warning("load_results: db file not found", db=str(db_path))
        return []

    clauses: list[str] = []
    params: list = []

    if run_date is not None:
        clauses.append("date = ?")
        params.append(str(run_date)[:10])

    if min_quality is not None and min_quality in _QUALITY_RANK:
        min_rank = _QUALITY_RANK[min_quality]
        qualifying = [q for q, rank in _QUALITY_RANK.items() if rank >= min_rank]
        placeholders = ",".join("?" * len(qualifying))
        clauses.append(f"setup_quality IN ({placeholders})")
        params.extend(qualifying)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM sepa_results {where} ORDER BY score DESC"  # noqa: S608

    try:
        with _connect(db_path) as conn:
            # Table may not exist on a fresh db that was never written to.
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]
    except sqlite3.Error as exc:
        log.error("load_results query failed", db=str(db_path), error=str(exc))
        return []
