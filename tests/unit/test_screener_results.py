"""
tests/unit/test_screener_results.py
────────────────────────────────────
Unit tests for screener/results.py:
    create_table(), persist_results(), load_results()

All tests use a tmp_path SQLite DB — no disk state survives between tests.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from screener.results import create_table, load_results, persist_results
from rules.scorer import SEPAResult


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_result(
    symbol: str = "TEST",
    score: float = 75.0,
    stage: int = 2,
    setup_quality: str = "A",
    trend_template_pass: bool = True,
    vcp_qualified: bool = True,
    breakout_triggered: bool = True,
    entry_price: float | None = 100.0,
    stop_loss: float | None = 92.0,
    risk_pct: float | None = 8.0,
    run_date: datetime.date | None = None,
) -> SEPAResult:
    return SEPAResult(
        symbol=symbol,
        date=run_date or datetime.date(2024, 1, 15),
        stage=stage,
        stage_label="Stage 2" if stage == 2 else f"Stage {stage}",
        stage_confidence=80,
        trend_template_pass=trend_template_pass,
        trend_template_details={f"C{i}": True for i in range(1, 9)},
        conditions_met=6,
        vcp_qualified=vcp_qualified,
        vcp_grade="A" if vcp_qualified else "FAIL",
        vcp_details={"contraction_count": 3, "vol_ratio": 0.4},
        breakout_triggered=breakout_triggered,
        entry_price=entry_price,
        stop_loss=stop_loss,
        stop_type="atr" if stop_loss is not None else None,
        risk_pct=risk_pct,
        rs_rating=85,
        setup_quality=setup_quality,
        score=score,
    )


# ─────────────────────────────────────────────────────────────────────────────
# create_table
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateTable:
    def test_creates_db_file(self, tmp_path):
        db = tmp_path / "test.db"
        create_table(db)
        assert db.exists()

    def test_idempotent(self, tmp_path):
        db = tmp_path / "test.db"
        create_table(db)
        create_table(db)  # must not raise

    def test_creates_parent_dirs(self, tmp_path):
        db = tmp_path / "deep" / "nested" / "test.db"
        create_table(db)
        assert db.exists()


# ─────────────────────────────────────────────────────────────────────────────
# persist_results
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistResults:
    def test_empty_list_does_not_crash(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([], db)  # must not raise

    def test_single_result_persisted(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([_make_result("AAPL")], db)
        rows = load_results(db)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "AAPL"

    def test_multiple_results_persisted(self, tmp_path):
        db = tmp_path / "test.db"
        results = [_make_result("AAPL"), _make_result("MSFT"), _make_result("GOOGL")]
        persist_results(results, db)
        rows = load_results(db)
        assert len(rows) == 3

    def test_duplicate_symbol_date_is_ignored(self, tmp_path):
        db = tmp_path / "test.db"
        r = _make_result("AAPL")
        persist_results([r], db)
        persist_results([r], db)  # same (symbol, date) — must not raise, not inserted twice
        rows = load_results(db)
        assert len(rows) == 1

    def test_same_symbol_different_date_both_inserted(self, tmp_path):
        db = tmp_path / "test.db"
        r1 = _make_result("AAPL", run_date=datetime.date(2024, 1, 15))
        r2 = _make_result("AAPL", run_date=datetime.date(2024, 1, 16))
        persist_results([r1, r2], db)
        rows = load_results(db)
        assert len(rows) == 2

    def test_score_stored_correctly(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([_make_result("AAPL", score=88.5)], db)
        rows = load_results(db)
        assert rows[0]["score"] == pytest.approx(88.5)

    def test_none_entry_price_stored_as_null(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([_make_result("AAPL", entry_price=None, stop_loss=None, risk_pct=None)], db)
        rows = load_results(db)
        assert rows[0]["entry_price"] is None

    def test_creates_table_automatically(self, tmp_path):
        db = tmp_path / "test.db"
        # persist_results without prior create_table must not raise
        persist_results([_make_result()], db)
        assert db.exists()


# ─────────────────────────────────────────────────────────────────────────────
# load_results
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadResults:
    def test_returns_empty_list_when_db_missing(self, tmp_path):
        db = tmp_path / "nonexistent.db"
        rows = load_results(db)
        assert rows == []

    def test_returns_list_of_dicts(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([_make_result()], db)
        rows = load_results(db)
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)

    def test_filter_by_date(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([
            _make_result("A", run_date=datetime.date(2024, 1, 15)),
            _make_result("B", run_date=datetime.date(2024, 1, 16)),
        ], db)
        rows = load_results(db, run_date="2024-01-15")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "A"

    def test_filter_by_min_quality_a_plus(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([
            _make_result("A", setup_quality="A+", score=100),
            _make_result("B", setup_quality="A",  score=80),
            _make_result("C", setup_quality="B",  score=60),
            _make_result("D", setup_quality="FAIL", score=0),
        ], db)
        rows = load_results(db, min_quality="A+")
        symbols = {r["symbol"] for r in rows}
        assert symbols == {"A"}

    def test_filter_by_min_quality_b(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([
            _make_result("A", setup_quality="A+", score=100),
            _make_result("B", setup_quality="A",  score=80),
            _make_result("C", setup_quality="B",  score=60),
            _make_result("D", setup_quality="FAIL", score=0),
        ], db)
        rows = load_results(db, min_quality="B")
        symbols = {r["symbol"] for r in rows}
        assert symbols == {"A", "B", "C"}

    def test_results_sorted_by_score_descending(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([
            _make_result("LOW",  score=40),
            _make_result("HIGH", score=95),
            _make_result("MID",  score=70),
        ], db)
        rows = load_results(db)
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_combined_date_and_quality_filter(self, tmp_path):
        db = tmp_path / "test.db"
        persist_results([
            _make_result("A", setup_quality="A+", score=100, run_date=datetime.date(2024, 1, 15)),
            _make_result("B", setup_quality="FAIL", score=10, run_date=datetime.date(2024, 1, 15)),
            _make_result("C", setup_quality="A+", score=90, run_date=datetime.date(2024, 1, 16)),
        ], db)
        rows = load_results(db, run_date="2024-01-15", min_quality="A+")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "A"
