"""
tests/fixtures/make_fixtures.py
───────────────────────────────
Script to (re-)generate the deterministic test fixtures that live in this
directory.  Run once after cloning the repo, or whenever fixture data needs
to be refreshed:

    cd /home/ubuntu/projects/minervini_ai
    python tests/fixtures/make_fixtures.py

Outputs:
    tests/fixtures/sample_ohlcv.parquet      — 300-row RELIANCE-like OHLCV
    tests/fixtures/sample_ohlcv_small.parquet — 10-row edge-case fixture
"""

from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

FIXTURE_DIR = Path(__file__).parent


def _make_ohlcv(n_rows: int, start: date | None = None) -> pd.DataFrame:
    start_date = start or date(2023, 1, 2)
    rows = []
    d = start_date
    price = 1_000.0
    for i in range(n_rows):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        noise = math.sin(i * 0.3) * 5
        open_  = round(price + noise, 2)
        close  = round(open_ * 1.0005, 2)
        high   = round(max(open_, close) * 1.005, 2)
        low    = round(min(open_, close) * 0.995, 2)
        volume = int(500_000 + (i % 50) * 10_000)
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


if __name__ == "__main__":
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    big = _make_ohlcv(300)
    big.to_parquet(FIXTURE_DIR / "sample_ohlcv.parquet", index=True, engine="pyarrow")
    print(f"Written: sample_ohlcv.parquet  ({len(big)} rows)")

    small = _make_ohlcv(10)
    small.to_parquet(FIXTURE_DIR / "sample_ohlcv_small.parquet", index=True, engine="pyarrow")
    print(f"Written: sample_ohlcv_small.parquet  ({len(small)} rows)")

    print("Fixtures ready.")
    sys.exit(0)
