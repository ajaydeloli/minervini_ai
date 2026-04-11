"""
tests/unit/test_sqlite_store.py
───────────────────────────────
Unit tests for storage/sqlite_store.py.

Coverage targets:
    create_tables()         — idempotent; all three tables created
    init_db()               — sets path, creates file, creates tables

    Watchlist CRUD:
        add_symbol()        — adds new, skips duplicate, uppercases
        remove_symbol()     — removes existing, returns False for missing
        symbol_in_watchlist() — True / False
        get_watchlist()     — returns all rows sorted correctly
        get_watchlist_symbols() — returns only symbol strings
        get_watchlist_symbol()  — single-symbol lookup / None
        bulk_add_symbols()  — counts added vs. skipped
        clear_watchlist()   — deletes all, returns count
        update_symbol_score() — updates last_score / last_quality

    Run history:
        log_run()           — inserts row with status='running', returns id
        finish_run()        — updates stats + status
        get_last_run()      — returns most recent (filtered / unfiltered)
        get_run_history()   — returns latest N rows

    Screener results:
        save_results()      — inserts rows, ON CONFLICT replace, in_watchlist flag
        get_results_for_date() — filtering by quality / watchlist_only
        get_top_results()   — watchlist symbols first, limited count
        get_symbol_history() — most-recent-first per symbol
        get_latest_result() — single most recent result / None

    Meta:
        get_meta()          — returns correct counts from live data
        db_path()           — returns Path object matching init_db arg
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import storage.sqlite_store as ss
from utils.exceptions import SQLiteError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

TODAY = date(2024, 6, 3)
TODAY_STR = "2024-06-03"


def _minimal_result(symbol: str, quality: str = "A+", score: float = 85.0) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "setup_quality": quality,
        "stage": 2,
        "trend_template_pass": True,
        "conditions_met": 8,
        "rs_rating": 80,
    }


# ─────────────────────────────────────────────────────────────────────────────
# init_db / create_tables
# ─────────────────────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_db_file(self, tmp_db_path: Path):
        assert tmp_db_path.exists()

    def test_creates_parent_dirs(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "test.db"
        ss.init_db(nested)
        assert nested.exists()

    def test_create_tables_idempotent(self, tmp_db_path: Path):
        """Calling create_tables() multiple times must not raise."""
        ss.create_tables()
        ss.create_tables()
        ss.create_tables()

    def test_db_path_returns_current_path(self, tmp_db_path: Path):
        assert ss.db_path() == tmp_db_path


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: add_symbol
# ─────────────────────────────────────────────────────────────────────────────

class TestAddSymbol:
    def test_add_returns_true(self, tmp_db_path: Path):
        result = ss.add_symbol("RELIANCE", added_via="cli")
        assert result is True

    def test_add_duplicate_returns_false(self, tmp_db_path: Path):
        ss.add_symbol("RELIANCE", added_via="cli")
        result = ss.add_symbol("RELIANCE", added_via="api")
        assert result is False

    def test_add_is_case_insensitive(self, tmp_db_path: Path):
        ss.add_symbol("reliance", added_via="cli")
        assert ss.symbol_in_watchlist("RELIANCE")

    def test_add_persists(self, tmp_db_path: Path):
        ss.add_symbol("TCS", added_via="cli")
        assert ss.symbol_in_watchlist("TCS")

    def test_add_with_note(self, tmp_db_path: Path):
        ss.add_symbol("DIXON", added_via="cli", note="strong VCP forming")
        row = ss.get_watchlist_symbol("DIXON")
        assert row is not None
        assert row["note"] == "strong VCP forming"

    def test_add_all_valid_sources(self, tmp_db_path: Path):
        """All five 'added_via' values must be accepted by the CHECK constraint."""
        for via, sym in zip(
            ["cli", "api", "dashboard", "file_upload", "test"],
            ["A", "B", "C", "D", "E"],
        ):
            ss.add_symbol(sym, added_via=via)


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: remove_symbol
# ─────────────────────────────────────────────────────────────────────────────

class TestRemoveSymbol:
    def test_remove_existing_returns_true(self, tmp_db_path: Path):
        ss.add_symbol("INFY", added_via="cli")
        assert ss.remove_symbol("INFY") is True

    def test_remove_missing_returns_false(self, tmp_db_path: Path):
        assert ss.remove_symbol("DOESNOTEXIST") is False

    def test_remove_then_not_in_watchlist(self, tmp_db_path: Path):
        ss.add_symbol("WIPRO", added_via="cli")
        ss.remove_symbol("WIPRO")
        assert not ss.symbol_in_watchlist("WIPRO")


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: symbol_in_watchlist
# ─────────────────────────────────────────────────────────────────────────────

class TestSymbolInWatchlist:
    def test_true_for_added(self, tmp_db_path: Path):
        ss.add_symbol("HDFCBANK", added_via="cli")
        assert ss.symbol_in_watchlist("HDFCBANK")

    def test_false_for_missing(self, tmp_db_path: Path):
        assert not ss.symbol_in_watchlist("NONEXISTENT")

    def test_case_insensitive(self, tmp_db_path: Path):
        ss.add_symbol("TITAN", added_via="cli")
        assert ss.symbol_in_watchlist("titan")


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: get_watchlist / get_watchlist_symbols
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWatchlist:
    def test_empty_returns_empty_list(self, tmp_db_path: Path):
        assert ss.get_watchlist() == []

    def test_returns_all_symbols(self, tmp_db_path: Path):
        for sym in ["TCS", "RELIANCE", "INFY"]:
            ss.add_symbol(sym, added_via="cli")
        result = ss.get_watchlist()
        symbols = [r["symbol"] for r in result]
        assert set(symbols) == {"TCS", "RELIANCE", "INFY"}

    def test_sort_by_symbol(self, tmp_db_path: Path):
        for sym in ["ZOMATO", "AAPL", "MSFT"]:
            ss.add_symbol(sym, added_via="cli")
        result = ss.get_watchlist(sort_by="symbol")
        names = [r["symbol"] for r in result]
        assert names == sorted(names)

    def test_get_watchlist_symbols_returns_strings(self, tmp_db_path: Path):
        ss.add_symbol("DIXON", added_via="cli")
        symbols = ss.get_watchlist_symbols()
        assert isinstance(symbols, list)
        assert all(isinstance(s, str) for s in symbols)

    def test_row_has_expected_keys(self, tmp_db_path: Path):
        ss.add_symbol("VOLTAS", added_via="api")
        rows = ss.get_watchlist()
        assert len(rows) == 1
        expected_keys = {"id", "symbol", "note", "added_at", "added_via",
                         "last_score", "last_quality", "last_run_at"}
        assert expected_keys.issubset(rows[0].keys())


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: get_watchlist_symbol
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWatchlistSymbol:
    def test_returns_dict_for_existing(self, tmp_db_path: Path):
        ss.add_symbol("TATAMOTORS", added_via="cli")
        row = ss.get_watchlist_symbol("TATAMOTORS")
        assert row is not None
        assert row["symbol"] == "TATAMOTORS"

    def test_returns_none_for_missing(self, tmp_db_path: Path):
        assert ss.get_watchlist_symbol("GHOST") is None


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: bulk_add_symbols
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkAddSymbols:
    def test_all_new_symbols_added(self, tmp_db_path: Path):
        result = ss.bulk_add_symbols(["TCS", "INFY", "WIPRO"], added_via="file_upload")
        assert result["added"] == 3
        assert result["skipped"] == 0

    def test_duplicates_counted_as_skipped(self, tmp_db_path: Path):
        ss.add_symbol("TCS", added_via="cli")
        result = ss.bulk_add_symbols(["TCS", "INFY"], added_via="api")
        assert result["added"] == 1
        assert result["skipped"] == 1

    def test_empty_list(self, tmp_db_path: Path):
        result = ss.bulk_add_symbols([], added_via="cli")
        assert result["added"] == 0
        assert result["skipped"] == 0

    def test_whitespace_stripped(self, tmp_db_path: Path):
        ss.bulk_add_symbols(["  RELIANCE  ", "TCS"], added_via="cli")
        assert ss.symbol_in_watchlist("RELIANCE")
        assert ss.symbol_in_watchlist("TCS")


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: clear_watchlist
# ─────────────────────────────────────────────────────────────────────────────

class TestClearWatchlist:
    def test_clears_all(self, tmp_db_path: Path):
        ss.bulk_add_symbols(["A", "B", "C"], added_via="cli")
        removed = ss.clear_watchlist()
        assert removed == 3
        assert ss.get_watchlist() == []

    def test_empty_watchlist_returns_zero(self, tmp_db_path: Path):
        assert ss.clear_watchlist() == 0


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist: update_symbol_score
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateSymbolScore:
    def test_updates_score_and_quality(self, tmp_db_path: Path):
        ss.add_symbol("DIXON", added_via="cli")
        ss.update_symbol_score("DIXON", score=91.5, quality="A+")
        row = ss.get_watchlist_symbol("DIXON")
        assert row["last_score"] == 91.5
        assert row["last_quality"] == "A+"

    def test_sets_last_run_at(self, tmp_db_path: Path):
        ss.add_symbol("TATAELXSI", added_via="cli")
        ts = datetime(2024, 6, 3, 15, 35, 0, tzinfo=timezone.utc)
        ss.update_symbol_score("TATAELXSI", score=70.0, quality="A", run_at=ts)
        row = ss.get_watchlist_symbol("TATAELXSI")
        assert row["last_run_at"] is not None

    def test_score_none_allowed(self, tmp_db_path: Path):
        """NULL score is valid for a symbol that failed screening."""
        ss.add_symbol("XYZ", added_via="cli")
        ss.update_symbol_score("XYZ", score=None, quality="FAIL")
        row = ss.get_watchlist_symbol("XYZ")
        assert row["last_quality"] == "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# Run history: log_run / finish_run
# ─────────────────────────────────────────────────────────────────────────────

class TestLogRun:
    def test_log_run_returns_int_id(self, tmp_db_path: Path):
        run_id = ss.log_run(TODAY, run_mode="daily")
        assert isinstance(run_id, int)
        assert run_id > 0

    def test_log_run_status_is_running(self, tmp_db_path: Path):
        run_id = ss.log_run(TODAY, run_mode="daily")
        row = ss.get_last_run()
        assert row is not None
        assert row["status"] == "running"
        assert row["id"] == run_id

    def test_log_run_stores_fields(self, tmp_db_path: Path):
        ss.log_run(
            TODAY,
            run_mode="manual",
            scope="watchlist",
            git_sha="abc123",
            universe_size=500,
            watchlist_size=15,
        )
        row = ss.get_last_run()
        assert row["run_date"] == TODAY_STR
        assert row["run_mode"] == "manual"
        assert row["scope"] == "watchlist"
        assert row["git_sha"] == "abc123"
        assert row["universe_size"] == 500

    def test_sequential_ids_increment(self, tmp_db_path: Path):
        id1 = ss.log_run(TODAY, run_mode="daily")
        id2 = ss.log_run(TODAY, run_mode="daily")
        assert id2 > id1


class TestFinishRun:
    def test_finish_updates_status(self, tmp_db_path: Path):
        run_id = ss.log_run(TODAY, run_mode="daily")
        ss.finish_run(run_id, status="success", duration_sec=28.4)
        row = ss.get_last_run()
        assert row["status"] == "success"
        assert abs(row["duration_sec"] - 28.4) < 0.01

    def test_finish_updates_counts(self, tmp_db_path: Path):
        run_id = ss.log_run(TODAY, run_mode="daily")
        ss.finish_run(
            run_id,
            status="success",
            passed_stage2=120,
            passed_tt=45,
            vcp_qualified=12,
            a_plus_count=3,
            a_count=9,
        )
        row = ss.get_last_run()
        assert row["passed_stage2"] == 120
        assert row["a_plus_count"] == 3
        assert row["a_count"] == 9

    def test_finish_failed_with_error_msg(self, tmp_db_path: Path):
        run_id = ss.log_run(TODAY, run_mode="daily")
        ss.finish_run(run_id, status="failed", error_msg="Network timeout")
        row = ss.get_last_run()
        assert row["status"] == "failed"
        assert "timeout" in row["error_msg"].lower()

    def test_finish_sets_finished_at(self, tmp_db_path: Path):
        run_id = ss.log_run(TODAY, run_mode="daily")
        ss.finish_run(run_id, status="success")
        row = ss.get_last_run()
        assert row["finished_at"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Run history: get_last_run / get_run_history
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRunHistory:
    def test_get_last_run_none_when_empty(self, tmp_db_path: Path):
        assert ss.get_last_run() is None

    def test_get_last_run_filters_by_mode(self, tmp_db_path: Path):
        ss.log_run(TODAY, run_mode="daily")
        ss.log_run(TODAY, run_mode="manual")
        row = ss.get_last_run(mode="manual")
        assert row["run_mode"] == "manual"

    def test_get_last_run_filters_by_status(self, tmp_db_path: Path):
        id1 = ss.log_run(TODAY, run_mode="daily")
        ss.finish_run(id1, status="success")
        ss.log_run(TODAY, run_mode="daily")   # still 'running'
        row = ss.get_last_run(status="success")
        assert row["status"] == "success"

    def test_get_run_history_returns_list(self, tmp_db_path: Path):
        for _ in range(5):
            ss.log_run(TODAY, run_mode="daily")
        history = ss.get_run_history(limit=3)
        assert len(history) == 3

    def test_get_run_history_most_recent_first(self, tmp_db_path: Path):
        id1 = ss.log_run(TODAY, run_mode="daily")
        id2 = ss.log_run(TODAY, run_mode="daily")
        history = ss.get_run_history()
        assert history[0]["id"] == id2   # most recent first
        assert history[1]["id"] == id1

    def test_get_run_history_filter_mode(self, tmp_db_path: Path):
        ss.log_run(TODAY, run_mode="daily")
        ss.log_run(TODAY, run_mode="backtest")
        ss.log_run(TODAY, run_mode="daily")
        history = ss.get_run_history(mode="backtest")
        assert len(history) == 1
        assert history[0]["run_mode"] == "backtest"


# ─────────────────────────────────────────────────────────────────────────────
# Screener results: save_results
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveResults:
    def test_save_returns_count(self, tmp_db_path: Path, sample_results_list: list):
        n = ss.save_results(sample_results_list, run_date=TODAY)
        assert n == len(sample_results_list)

    def test_save_idempotent_on_re_run(self, tmp_db_path: Path, sample_result: dict):
        ss.save_results([sample_result], run_date=TODAY)
        ss.save_results([sample_result], run_date=TODAY)   # second write same day
        results = ss.get_results_for_date(TODAY)
        assert len(results) == 1     # ON CONFLICT REPLACE, not duplicate

    def test_save_updates_on_conflict(self, tmp_db_path: Path, sample_result: dict):
        ss.save_results([sample_result], run_date=TODAY)
        updated = {**sample_result, "score": 99.0, "setup_quality": "A+"}
        ss.save_results([updated], run_date=TODAY)
        result = ss.get_results_for_date(TODAY)
        assert result[0]["score"] == 99.0

    def test_in_watchlist_flag_set(self, tmp_db_path: Path, sample_result: dict):
        ss.save_results(
            [sample_result],
            run_date=TODAY,
            watchlist_symbols={"DIXON"},
        )
        results = ss.get_results_for_date(TODAY)
        assert results[0]["in_watchlist"] == 1

    def test_in_watchlist_flag_unset(self, tmp_db_path: Path, sample_result: dict):
        ss.save_results([sample_result], run_date=TODAY, watchlist_symbols=set())
        results = ss.get_results_for_date(TODAY)
        assert results[0]["in_watchlist"] == 0

    def test_symbol_uppercased_in_db(self, tmp_db_path: Path):
        result = {**_minimal_result("reliance")}
        ss.save_results([result], run_date=TODAY)
        rows = ss.get_results_for_date(TODAY)
        assert rows[0]["symbol"] == "RELIANCE"

    def test_result_json_blob_stored(self, tmp_db_path: Path, sample_result: dict):
        ss.save_results([sample_result], run_date=TODAY)
        rows = ss.get_results_for_date(TODAY)
        assert rows[0]["result_json"] is not None
        import json
        blob = json.loads(rows[0]["result_json"])
        assert blob["symbol"] == "DIXON"


# ─────────────────────────────────────────────────────────────────────────────
# Screener results: get_results_for_date
# ─────────────────────────────────────────────────────────────────────────────

class TestGetResultsForDate:
    def _populate(self, tmp_db_path: Path):
        results = [
            _minimal_result("APLUS1", "A+", 90.0),
            _minimal_result("APLUS2", "A+", 85.0),
            _minimal_result("AGRADE", "A",  72.0),
            _minimal_result("BGRADE", "B",  55.0),
            _minimal_result("FAILED", "FAIL", 20.0),
        ]
        ss.save_results(results, run_date=TODAY, watchlist_symbols={"APLUS1"})

    def test_returns_all_when_no_filter(self, tmp_db_path: Path):
        self._populate(tmp_db_path)
        rows = ss.get_results_for_date(TODAY)
        assert len(rows) == 5

    def test_min_quality_a_plus_filters(self, tmp_db_path: Path):
        self._populate(tmp_db_path)
        rows = ss.get_results_for_date(TODAY, min_quality="A+")
        qualities = {r["setup_quality"] for r in rows}
        assert qualities == {"A+"}

    def test_min_quality_a_includes_a_plus(self, tmp_db_path: Path):
        self._populate(tmp_db_path)
        rows = ss.get_results_for_date(TODAY, min_quality="A")
        qualities = {r["setup_quality"] for r in rows}
        assert qualities == {"A+", "A"}

    def test_watchlist_only_filter(self, tmp_db_path: Path):
        self._populate(tmp_db_path)
        rows = ss.get_results_for_date(TODAY, watchlist_only=True)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "APLUS1"

    def test_empty_date_returns_empty(self, tmp_db_path: Path):
        rows = ss.get_results_for_date(date(2099, 1, 1))
        assert rows == []


# ─────────────────────────────────────────────────────────────────────────────
# Screener results: get_top_results
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTopResults:
    def test_watchlist_symbols_first(self, tmp_db_path: Path):
        """
        Within the same quality tier, watchlist symbols must appear before
        non-watchlist symbols even if their scores are lower.
        Design doc §6.5: 'Watchlist symbols appear first in the daily report'.
        """
        results = [
            _minimal_result("UNIVERSE_HIGH", "A+", 95.0),  # high score, not in WL
            _minimal_result("WATCHLIST_MED", "A+", 75.0),  # lower score, in WL
        ]
        ss.save_results(results, run_date=TODAY, watchlist_symbols={"WATCHLIST_MED"})
        top = ss.get_top_results(TODAY, limit=10)
        # Watchlist symbol must come first
        assert top[0]["symbol"] == "WATCHLIST_MED"
        assert top[0]["in_watchlist"] == 1

    def test_limit_respected(self, tmp_db_path: Path):
        results = [_minimal_result(f"SYM{i}", "A+", 80.0 - i) for i in range(10)]
        ss.save_results(results, run_date=TODAY)
        top = ss.get_top_results(TODAY, limit=3)
        assert len(top) == 3

    def test_min_quality_excludes_below(self, tmp_db_path: Path):
        results = [
            _minimal_result("GOOD", "A+", 90.0),
            _minimal_result("POOR", "FAIL", 10.0),
        ]
        ss.save_results(results, run_date=TODAY)
        top = ss.get_top_results(TODAY, min_quality="B")
        syms = [r["symbol"] for r in top]
        assert "POOR" not in syms


# ─────────────────────────────────────────────────────────────────────────────
# Screener results: get_symbol_history / get_latest_result
# ─────────────────────────────────────────────────────────────────────────────

class TestSymbolHistory:
    def test_history_most_recent_first(self, tmp_db_path: Path):
        d1 = date(2024, 6, 1)
        d2 = date(2024, 6, 2)
        d3 = date(2024, 6, 3)
        ss.save_results([_minimal_result("RELIANCE", "A", 70.0)], run_date=d1)
        ss.save_results([_minimal_result("RELIANCE", "A+", 80.0)], run_date=d2)
        ss.save_results([_minimal_result("RELIANCE", "B", 55.0)], run_date=d3)

        history = ss.get_symbol_history("RELIANCE", days=10)
        assert len(history) == 3
        # most recent first
        assert history[0]["run_date"] == str(d3)

    def test_history_respects_days_limit(self, tmp_db_path: Path):
        for i in range(5):
            ss.save_results([_minimal_result("TCS")], run_date=date(2024, 6, i + 1))
        history = ss.get_symbol_history("TCS", days=2)
        assert len(history) == 2

    def test_history_empty_for_unknown_symbol(self, tmp_db_path: Path):
        assert ss.get_symbol_history("GHOST") == []

    def test_history_case_insensitive(self, tmp_db_path: Path):
        ss.save_results([_minimal_result("INFY")], run_date=TODAY)
        history = ss.get_symbol_history("infy")
        assert len(history) == 1


class TestGetLatestResult:
    def test_returns_most_recent(self, tmp_db_path: Path):
        d1 = date(2024, 5, 1)
        d2 = date(2024, 6, 1)
        ss.save_results([_minimal_result("WIPRO", "B", 55.0)], run_date=d1)
        ss.save_results([_minimal_result("WIPRO", "A+", 90.0)], run_date=d2)
        latest = ss.get_latest_result("WIPRO")
        assert latest["run_date"] == str(d2)
        assert latest["score"] == 90.0

    def test_returns_none_for_unknown(self, tmp_db_path: Path):
        assert ss.get_latest_result("UNKNOWN") is None


# ─────────────────────────────────────────────────────────────────────────────
# get_meta
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMeta:
    def test_meta_empty_db(self, tmp_db_path: Path):
        meta = ss.get_meta()
        assert meta["watchlist_size"] == 0
        assert meta["last_screen_date"] is None
        assert meta["a_plus_count"] is None

    def test_meta_reflects_watchlist_size(self, tmp_db_path: Path):
        ss.bulk_add_symbols(["A", "B", "C"], added_via="cli")
        meta = ss.get_meta()
        assert meta["watchlist_size"] == 3

    def test_meta_reflects_last_run(self, tmp_db_path: Path):
        run_id = ss.log_run(TODAY, run_mode="daily")
        ss.finish_run(run_id, status="success", a_plus_count=3, a_count=7)
        meta = ss.get_meta()
        assert meta["last_screen_date"] == TODAY_STR
        assert meta["a_plus_count"] == 3
        assert meta["a_count"] == 7
        assert meta["last_run_status"] == "success"

    def test_meta_excludes_running_status(self, tmp_db_path: Path):
        """get_meta() must not return a row with status='running'."""
        # First a successful run
        run_id = ss.log_run(date(2024, 5, 1), run_mode="daily")
        ss.finish_run(run_id, status="success", a_plus_count=1, a_count=2)
        # Then start a new run but don't finish it
        ss.log_run(TODAY, run_mode="daily")   # still 'running'

        meta = ss.get_meta()
        assert meta["last_run_status"] == "success"   # not 'running'
