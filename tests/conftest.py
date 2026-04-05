"""
tests/conftest.py
─────────────────
Shared pytest fixtures available to all test modules.

Fixtures defined here:
    tmp_parquet_path    — a fresh .parquet path inside a tmp_path
    sample_ohlcv_df     — deterministic 300-row OHLCV DataFrame (RELIANCE-like)
    small_ohlcv_df      — 10-row DataFrame for edge-case / insufficient-data tests
    tmp_db_path         — SQLite path inside tmp_path; init_db() already called
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

# ── project imports ──────────────────────────────────────────────────────────
# We import storage lazily inside fixtures so that module-level import
# errors don't mask fixture setup failures.


# ─────────────────────────────────────────────────────────────────────────────
# OHLCV helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n_rows: int, start: date | None = None) -> pd.DataFrame:
    """
    Build a deterministic OHLCV DataFrame with *n_rows* rows.

    Price model: starts at 1 000 INR, gentle uptrend (+0.05 % per day)
    with small sinusoidal noise so MAs won't be perfectly flat.

    Columns: open, high, low, close, volume
    Index:   DatetimeIndex named 'date' (Mon–Fri only, skips weekends)
    """
    start_date = start or date(2023, 1, 2)  # Monday

    rows = []
    d = start_date
    price = 1_000.0
    for _ in range(n_rows):
        # skip weekends
        while d.weekday() >= 5:
            d += timedelta(days=1)

        noise = math.sin(_ * 0.3) * 5          # ±5 INR sinusoidal noise
        open_  = round(price + noise, 2)
        close  = round(open_ * 1.0005, 2)       # 0.05 % daily drift
        high   = round(max(open_, close) * 1.005, 2)
        low    = round(min(open_, close) * 0.995, 2)
        volume = int(500_000 + (_ % 50) * 10_000)  # 500k–990k, deterministic

        rows.append({
            "date":   pd.Timestamp(d),
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": volume,
        })

        price = close
        d += timedelta(days=1)

    df = pd.DataFrame(rows).set_index("date")
    df.index.name = "date"
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def sample_ohlcv_df() -> pd.DataFrame:
    """300-row OHLCV DataFrame — enough history for SMA200 + buffer."""
    return _make_ohlcv(300)


@pytest.fixture()
def small_ohlcv_df() -> pd.DataFrame:
    """10-row DataFrame — used for insufficient-data / edge-case tests."""
    return _make_ohlcv(10)


@pytest.fixture()
def tmp_parquet_path(tmp_path: Path) -> Path:
    """A non-existent .parquet path inside pytest's temporary directory."""
    return tmp_path / "test_symbol.parquet"


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> Path:
    """
    Initialised SQLite database inside pytest's tmp_path.
    Each test gets a fresh, empty database.
    """
    import storage.sqlite_store as ss

    db = tmp_path / "test_minervini.db"
    ss.init_db(db)
    yield db
    # cleanup handled by tmp_path teardown


@pytest.fixture()
def sample_result() -> dict:
    """
    A minimal SEPAResult-like dict suitable for save_results().
    All optional numeric fields included with realistic values.
    """
    return {
        "symbol": "DIXON",
        "score": 88.5,
        "setup_quality": "A+",
        "stage": 2,
        "stage_label": "Stage 2 — Advancing",
        "stage_confidence": 85,
        "trend_template_pass": True,
        "conditions_met": 8,
        "fundamental_pass": True,
        "vcp_qualified": True,
        "breakout_triggered": True,
        "entry_price": 14200.0,
        "stop_loss": 13100.0,
        "risk_pct": 7.7,
        "rs_rating": 88,
        "news_score": 15.0,
    }


@pytest.fixture()
def sample_results_list(sample_result: dict) -> list[dict]:
    """
    Three SEPAResult-like dicts covering different quality tiers.
    """
    return [
        sample_result,  # A+, score 88.5, DIXON
        {
            "symbol": "TCS",
            "score": 72.0,
            "setup_quality": "A",
            "stage": 2,
            "trend_template_pass": True,
            "conditions_met": 8,
            "rs_rating": 75,
        },
        {
            "symbol": "INFY",
            "score": 55.0,
            "setup_quality": "B",
            "stage": 2,
            "trend_template_pass": True,
            "conditions_met": 6,
            "rs_rating": 60,
        },
    ]
