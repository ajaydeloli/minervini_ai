"""
tests/unit/test_daily_watchlist.py
────────────────────────────────────
Unit tests for reports/daily_watchlist.py.

All tests use the pytest tmp_path fixture so no file I/O touches the real
project tree.  Results are built as plain dicts — no live DB or screener
dependencies required.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from reports.daily_watchlist import generate_watchlist, WatchlistOutput

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

_RUN_DATE = "2024-01-15"

_BASE_CONFIG: dict = {
    "watchlist": {
        "priority_in_reports": True,
    }
}


def _make_result(
    symbol: str,
    score: int,
    setup_quality: str = "B",
    in_watchlist: bool = False,
    stage: int = 2,
    rs_rating: int = 75,
    vcp_qualified: bool = True,
    breakout_triggered: bool = False,
    entry_price: float | None = 100.0,
    stop_loss: float | None = 92.0,
    risk_pct: float | None = 8.0,
) -> dict:
    """Return a minimal result dict compatible with generate_watchlist()."""
    return {
        "symbol":            symbol,
        "score":             score,
        "setup_quality":     setup_quality,
        "in_watchlist":      in_watchlist,
        "stage":             stage,
        "rs_rating":         rs_rating,
        "vcp_qualified":     vcp_qualified,
        "breakout_triggered": breakout_triggered,
        "entry_price":       entry_price,
        "stop_loss":         stop_loss,
        "risk_pct":          risk_pct,
    }


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_csv_contains_correct_columns(tmp_path: Path) -> None:
    """CSV must contain exactly the canonical column set (in any order)."""
    results = [
        _make_result("DIXON",    score=88, setup_quality="A+"),
        _make_result("RELIANCE", score=72, setup_quality="A"),
        _make_result("INFY",     score=58, setup_quality="B"),
    ]

    output = generate_watchlist(
        run_date=_RUN_DATE,
        results=results,
        config=_BASE_CONFIG,
        output_dir=tmp_path,
    )

    assert output.csv_path.exists()
    rows = _read_csv(output.csv_path)
    assert len(rows) == 3

    expected_columns = {
        "rank", "symbol", "score", "setup_quality", "stage",
        "rs_rating", "vcp_qualified", "breakout_triggered",
        "entry_price", "stop_loss", "risk_pct", "in_watchlist",
        "fundamental_pass", "fundamental_details", "news_score",
    }
    assert set(rows[0].keys()) == expected_columns


def test_html_file_created(tmp_path: Path) -> None:
    """HTML file must exist and contain key structural markers."""
    results = [
        _make_result("DIXON",    score=88, setup_quality="A+"),
        _make_result("RELIANCE", score=72, setup_quality="A"),
    ]

    output = generate_watchlist(
        run_date=_RUN_DATE,
        results=results,
        config=_BASE_CONFIG,
        output_dir=tmp_path,
    )

    assert output.html_path.exists()
    html = output.html_path.read_text(encoding="utf-8")

    # Date appears in the title / header
    assert _RUN_DATE in html
    # Both symbols appear in the output
    assert "DIXON" in html
    assert "RELIANCE" in html
    # Badge markers are present
    assert "A+" in html
    assert "A" in html
    # WatchlistOutput counts match
    assert output.a_plus_count == 1
    assert output.a_count == 1
    assert output.total_count == 2


def test_watchlist_symbols_sorted_first(tmp_path: Path) -> None:
    """
    When priority_in_reports=True, watchlist symbols appear before non-watchlist
    symbols regardless of their relative scores.
    """
    results = [
        _make_result("HIGHSCORE_NON_WL", score=95, in_watchlist=False),
        _make_result("LOWSCORE_WL",      score=55, in_watchlist=True),
        _make_result("MID_NON_WL",       score=70, in_watchlist=False),
    ]

    output = generate_watchlist(
        run_date=_RUN_DATE,
        results=results,
        config=_BASE_CONFIG,
        output_dir=tmp_path,
    )

    rows = _read_csv(output.csv_path)
    symbols = [r["symbol"] for r in rows]

    # Watchlist symbol must be first
    assert symbols[0] == "LOWSCORE_WL"
    # Non-watchlist ordered by score desc
    assert symbols[1] == "HIGHSCORE_NON_WL"
    assert symbols[2] == "MID_NON_WL"

    # Rank column should be sequential
    assert [r["rank"] for r in rows] == ["1", "2", "3"]


def test_watchlist_symbols_sorted_first_via_set(tmp_path: Path) -> None:
    """
    When in_watchlist is absent, watchlist_symbols set is used to determine
    priority sorting.
    """
    results = [
        {"symbol": "TOPDOG",  "score": 90, "setup_quality": "A+"},
        {"symbol": "WL_SYM",  "score": 60, "setup_quality": "B"},
        {"symbol": "MIDRANGE", "score": 75, "setup_quality": "A"},
    ]

    output = generate_watchlist(
        run_date=_RUN_DATE,
        results=results,
        config=_BASE_CONFIG,
        output_dir=tmp_path,
        watchlist_symbols={"WL_SYM"},
    )

    rows = _read_csv(output.csv_path)
    assert rows[0]["symbol"] == "WL_SYM", "Watchlist symbol should be ranked first"
    assert rows[0]["in_watchlist"] == "True"


def test_empty_results_produces_files(tmp_path: Path) -> None:
    """An empty result list must still produce both files without error."""
    output = generate_watchlist(
        run_date=_RUN_DATE,
        results=[],
        config=_BASE_CONFIG,
        output_dir=tmp_path,
    )

    assert isinstance(output, WatchlistOutput)
    assert output.csv_path.exists()
    assert output.html_path.exists()
    assert output.a_plus_count == 0
    assert output.a_count == 0
    assert output.total_count == 0

    # CSV should have a header row and no data rows
    rows = _read_csv(output.csv_path)
    assert rows == []

    # HTML should still mention the run date
    html = output.html_path.read_text(encoding="utf-8")
    assert _RUN_DATE in html
