"""
tests/unit/test_run_history.py
───────────────────────────────
Unit tests for run_history persistence and run-metadata helpers.

Tests
─────
1.  log_run() + finish_run() → row exists with every field correctly stored.
2.  get_git_sha()            → returns a non-empty string (type check + length).
3.  get_config_hash()        → returns exactly 8 lowercase hex characters.
4.  finish_run(unknown_id)   → no exception raised (graceful no-op).
5.  get_last_run()           → returns the most recent row after two log_run() calls.
"""

from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path

import pytest

# ── project imports ──────────────────────────────────────────────────────────
import storage.sqlite_store as ss
from utils.run_meta import get_config_hash, get_git_sha


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Full round-trip: log_run → finish_run → verify stored row
# ─────────────────────────────────────────────────────────────────────────────

def test_log_and_finish_run_stores_all_fields(tmp_db_path: Path) -> None:
    """
    A complete log_run() / finish_run() cycle must persist every column
    that the pipeline writes.  Verifies no field silently stays NULL.
    """
    ss.init_db(tmp_db_path)

    run_id = ss.log_run(
        run_date=date(2026, 4, 11),
        run_mode="daily",
        scope="all",
        git_sha="abc12345",
        config_hash="d41d8cd9",
        universe_size=500,
        watchlist_size=20,
    )

    assert isinstance(run_id, int), "log_run() must return an integer run_id"

    # Immediately after log_run the status must be 'running'
    with ss._connect() as conn:  # noqa: SLF001
        row = dict(conn.execute(
            "SELECT * FROM run_history WHERE id = ?", (run_id,)
        ).fetchone())

    assert row["status"] == "running"
    assert row["run_date"] == "2026-04-11"
    assert row["run_mode"] == "daily"
    assert row["git_sha"] == "abc12345"
    assert row["config_hash"] == "d41d8cd9"
    assert row["universe_size"] == 500
    assert row["watchlist_size"] == 20

    # Now finish the run
    ss.finish_run(
        run_id=run_id,
        status="success",
        duration_sec=42.5,
        passed_stage2=120,
        passed_tt=80,
        vcp_qualified=15,
        a_plus_count=5,
        a_count=10,
        error_msg=None,
    )

    with ss._connect() as conn:  # noqa: SLF001
        row = dict(conn.execute(
            "SELECT * FROM run_history WHERE id = ?", (run_id,)
        ).fetchone())

    assert row["status"] == "success"
    assert row["duration_sec"] == pytest.approx(42.5)
    assert row["passed_stage2"] == 120
    assert row["passed_tt"] == 80
    assert row["vcp_qualified"] == 15
    assert row["a_plus_count"] == 5
    assert row["a_count"] == 10
    assert row["error_msg"] is None
    assert row["finished_at"] is not None, "finished_at must be set by finish_run()"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — get_git_sha() returns a non-empty string
# ─────────────────────────────────────────────────────────────────────────────

def test_get_git_sha_returns_nonempty_string() -> None:
    """
    get_git_sha() must always return a non-empty str.
    In a git repo it returns the short SHA; outside one it returns 'unknown'.
    Either way the type and non-emptiness invariants must hold.
    """
    sha = get_git_sha()
    assert isinstance(sha, str), "get_git_sha() must return str"
    assert len(sha) > 0, "get_git_sha() must never return an empty string"
    # If it looks like a real SHA it should be hex; if fallback it's 'unknown'
    if sha != "unknown":
        assert re.fullmatch(r"[0-9a-f]+", sha), (
            f"git SHA should be lowercase hex, got: {sha!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — get_config_hash() returns exactly 8 lowercase hex chars
# ─────────────────────────────────────────────────────────────────────────────

def test_get_config_hash_returns_8_hex_chars() -> None:
    """
    get_config_hash() must return exactly 8 lowercase hex characters when
    called with a valid file, and 'unknown' when the file doesn't exist.
    """
    # Happy path: write a temp file with known content
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as fh:
        fh.write(b"key: value\n")
        tmp_path = Path(fh.name)

    try:
        digest = get_config_hash(tmp_path)
        assert re.fullmatch(r"[0-9a-f]{8}", digest), (
            f"Expected 8 hex chars, got: {digest!r}"
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    # Error path: non-existent file → 'unknown'
    bad_hash = get_config_hash("/nonexistent/path/settings.yaml")
    assert bad_hash == "unknown", (
        f"Expected 'unknown' for missing file, got: {bad_hash!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — finish_run() on an unknown run_id raises no exception
# ─────────────────────────────────────────────────────────────────────────────

def test_finish_run_unknown_id_does_not_raise(tmp_db_path: Path) -> None:
    """
    Calling finish_run() with a run_id that does not exist in the database
    must not raise any exception — it should silently update 0 rows.

    This guards against race conditions where log_run() failed but the
    pipeline still attempts finish_run() at teardown.
    """
    ss.init_db(tmp_db_path)

    # 99999 is very unlikely to collide with any real auto-increment id
    try:
        ss.finish_run(
            run_id=99999,
            status="success",
            duration_sec=1.0,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"finish_run() with unknown run_id raised an unexpected exception: {exc}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — get_last_run() returns the most recent row after two log_run() calls
# ─────────────────────────────────────────────────────────────────────────────

def test_get_last_run_returns_most_recent(tmp_db_path: Path) -> None:
    """
    After two log_run() calls, get_last_run() must return the second one
    (highest id), confirming ORDER BY id DESC LIMIT 1 semantics.
    """
    ss.init_db(tmp_db_path)

    first_id = ss.log_run(
        run_date=date(2026, 4, 10),
        run_mode="daily",
        git_sha="aaaaaaaa",
    )
    second_id = ss.log_run(
        run_date=date(2026, 4, 11),
        run_mode="manual",
        git_sha="bbbbbbbb",
    )

    assert second_id > first_id, "second log_run() id must be greater than first"

    last = ss.get_last_run()
    assert last is not None, "get_last_run() must not return None after two inserts"
    assert last["id"] == second_id, (
        f"get_last_run() should return id={second_id}, got id={last['id']}"
    )
    assert last["run_date"] == "2026-04-11"
    assert last["run_mode"] == "manual"
    assert last["git_sha"] == "bbbbbbbb"
