"""
tests/unit/test_nse_bhav.py
────────────────────────────
Unit tests for ingestion/nse_bhav.py (NSEBhavSource).

All HTTP calls are mocked via unittest.mock — no real network requests are made.

Minimum 12 required tests (spec):
  1.  fetch() returns correct OHLCV DataFrame for a date range
  2.  fetch() filters out non-EQ series rows
  3.  fetch() skips dates where file returns 404 (holiday)
  4.  fetch() raises DataFetchError on 5xx HTTP after retries
  5.  fetch() uses disk cache and does NOT make HTTP request for cached dates
  6.  fetch() raises InsufficientDataError when result < MIN_USABLE_ROWS (bootstrap)
  7.  fetch_universe() returns sorted list of EQ symbols from today's Bhavcopy
  8.  fetch_universe() falls back to yfinance universe on HTTP failure
  9.  fetch_single_day() returns exactly one row for a valid trading date
  10. fetch_single_day() raises DataFetchError if symbol not in that day's file
  11. _build_url() generates the correct URL for a given date
  12. _parse_csv() correctly maps TIMESTAMP → DatetimeIndex and renames columns
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from ingestion.base import MIN_USABLE_ROWS, OHLCV_COLUMNS
from ingestion.nse_bhav import NSEBhavSource, _empty_ohlcv
from utils.exceptions import DataFetchError, InsufficientDataError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build fake Bhavcopy CSVs and ZIPs
# ─────────────────────────────────────────────────────────────────────────────

def _make_bhav_csv(rows: list[dict]) -> str:
    """Build a Bhavcopy-formatted CSV string from a list of row dicts."""
    header = (
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        "TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN"
    )
    lines = [header]
    for r in rows:
        line = (
            f"{r['SYMBOL']},{r.get('SERIES', 'EQ')},"
            f"{r['OPEN']},{r['HIGH']},{r['LOW']},{r['CLOSE']},"
            f"{r['CLOSE']},{r.get('PREVCLOSE', r['CLOSE'])},"
            f"{r['TOTTRDQTY']},0,{r['TIMESTAMP']},100,INE000A01001"
        )
        lines.append(line)
    return "\n".join(lines)


def _make_zip(csv_content: str, csv_name: str = "bhav.csv") -> bytes:
    """Create in-memory ZIP bytes containing one CSV file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, csv_content)
    return buf.getvalue()


def _single_row_zip(
    symbol: str = "RELIANCE",
    series: str = "EQ",
    timestamp: str = "15-JAN-2024",
    open_: float = 100.0,
    high: float = 110.0,
    low: float = 95.0,
    close: float = 105.0,
    volume: int = 1_000_000,
) -> bytes:
    """ZIP with a single row for the given symbol."""
    rows = [dict(
        SYMBOL=symbol, SERIES=series,
        OPEN=open_, HIGH=high, LOW=low, CLOSE=close,
        TOTTRDQTY=volume, TIMESTAMP=timestamp,
    )]
    return _make_zip(_make_bhav_csv(rows))


def _multi_symbol_zip(date_str: str = "15-JAN-2024") -> bytes:
    """
    ZIP with:
      - RELIANCE EQ  (volume=500000)
      - RELIANCE BE  (should be filtered out)
      - TCS EQ       (volume=200000)
    """
    rows = [
        dict(SYMBOL="RELIANCE", SERIES="EQ",  OPEN=100, HIGH=110, LOW=95,
             CLOSE=105, TOTTRDQTY=500_000, TIMESTAMP=date_str),
        dict(SYMBOL="RELIANCE", SERIES="BE",  OPEN=100, HIGH=110, LOW=95,
             CLOSE=105, TOTTRDQTY=10_000,  TIMESTAMP=date_str),
        dict(SYMBOL="TCS",      SERIES="EQ",  OPEN=200, HIGH=220, LOW=195,
             CLOSE=210, TOTTRDQTY=200_000, TIMESTAMP=date_str),
    ]
    return _make_zip(_make_bhav_csv(rows))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def src(tmp_path: Path) -> NSEBhavSource:
    """NSEBhavSource with isolated temp cache dir and a minimal universe.yaml."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "universe.yaml").write_text(
        "symbols:\n  - RELIANCE\n  - TCS\n  - INFY\n"
    )
    return NSEBhavSource(
        cache_dir=tmp_path / "bhav",
        universe_yaml=cfg_dir / "universe.yaml",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — fetch() returns correct OHLCV DataFrame for a date range
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_returns_ohlcv_dataframe_for_date_range(src: NSEBhavSource) -> None:
    """fetch() assembles a correct multi-day OHLCV DataFrame."""
    zip_jan15 = _single_row_zip(symbol="RELIANCE", timestamp="15-JAN-2024", close=105.0)
    zip_jan16 = _single_row_zip(symbol="RELIANCE", timestamp="16-JAN-2024", close=107.0)

    def fake_download(url: str) -> bytes:
        if "cm15JAN2024" in url:
            return zip_jan15
        if "cm16JAN2024" in url:
            return zip_jan16
        err = requests.HTTPError()
        err.response = MagicMock(status_code=404)
        raise err

    with patch.object(src, "_download_with_retry", side_effect=fake_download):
        df = src.fetch("RELIANCE", start=date(2024, 1, 15), end=date(2024, 1, 16))

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == list(OHLCV_COLUMNS)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "date"
    assert len(df) == 2
    assert df.loc[pd.Timestamp("2024-01-15"), "close"] == pytest.approx(105.0)
    assert df.loc[pd.Timestamp("2024-01-16"), "close"] == pytest.approx(107.0)
    # Index is sorted ascending
    assert df.index.is_monotonic_increasing


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — fetch() filters out non-EQ series rows
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_filters_non_eq_series(src: NSEBhavSource) -> None:
    """fetch() only returns rows where SERIES == 'EQ'; BE rows must be dropped."""
    zip_data = _multi_symbol_zip("15-JAN-2024")

    with patch.object(src, "_download_with_retry", return_value=zip_data):
        df = src.fetch("RELIANCE", start=date(2024, 1, 15), end=date(2024, 1, 15))

    # Only the EQ row for RELIANCE should remain (volume=500000, not 10000)
    assert len(df) == 1
    assert df.iloc[0]["volume"] == pytest.approx(500_000)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — fetch() skips dates where file returns 404 (holiday/weekend)
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_skips_404_dates(src: NSEBhavSource) -> None:
    """fetch() silently skips trading dates that return HTTP 404."""
    zip_jan16 = _single_row_zip(symbol="TCS", timestamp="16-JAN-2024")

    def fake_download(url: str) -> bytes:
        if "cm15JAN2024" in url:
            err = requests.HTTPError()
            err.response = MagicMock(status_code=404)
            raise err
        return zip_jan16

    with patch.object(src, "_download_with_retry", side_effect=fake_download):
        df = src.fetch("TCS", start=date(2024, 1, 15), end=date(2024, 1, 16))

    assert len(df) == 1
    assert df.index[0] == pd.Timestamp("2024-01-16")


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — fetch() raises DataFetchError on 5xx HTTP after retries
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_raises_data_fetch_error_on_5xx(src: NSEBhavSource) -> None:
    """fetch() raises DataFetchError when the server returns HTTP 5xx."""
    err = requests.HTTPError()
    err.response = MagicMock(status_code=500)

    with patch.object(src, "_download_with_retry", side_effect=err):
        with pytest.raises(DataFetchError):
            src.fetch("RELIANCE", start=date(2024, 1, 15), end=date(2024, 1, 15))


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — fetch() uses disk cache and does NOT make HTTP request for cached dates
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_uses_disk_cache_skips_http(src: NSEBhavSource) -> None:
    """fetch() reads from disk cache; _download_with_retry must not be called."""
    d = date(2024, 1, 15)
    # Pre-populate the cache directory
    cache_path = src._build_cache_path(d)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(_single_row_zip(symbol="TCS", timestamp="15-JAN-2024"))

    with patch.object(src, "_download_with_retry") as mock_dl:
        df = src.fetch("TCS", start=d, end=d)

    mock_dl.assert_not_called()
    assert len(df) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — fetch() raises InsufficientDataError when result < MIN_USABLE_ROWS
#           (bootstrap mode — long date range, very few rows returned)
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_raises_insufficient_data_in_bootstrap_mode(src: NSEBhavSource) -> None:
    """
    fetch() raises InsufficientDataError when a long date range is requested
    but only a handful of rows are returned (newly-listed or thinly-traded symbol).
    """
    zip_data = _single_row_zip(symbol="RELIANCE", timestamp="15-JAN-2024")

    def fake_get_zip(d: date) -> bytes | None:
        # Return data only for the exact target date; all others are holidays
        if d == date(2024, 1, 15):
            return zip_data
        return None   # simulates 404 for every other day

    with patch.object(src, "_get_zip", side_effect=fake_get_zip):
        with pytest.raises(InsufficientDataError):
            # Request ~2 years (>MIN_USABLE_ROWS days) but only get 1 row back
            src.fetch(
                "RELIANCE",
                start=date(2022, 1, 1),
                end=date(2024, 1, 15),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — fetch_universe() returns sorted list of EQ symbols from today's Bhavcopy
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_universe_returns_sorted_eq_symbols(src: NSEBhavSource) -> None:
    """fetch_universe() returns a sorted list of all EQ symbols from today's file."""
    zip_data = _multi_symbol_zip()   # RELIANCE EQ, RELIANCE BE, TCS EQ

    with patch.object(src, "_get_zip", return_value=zip_data):
        symbols = src.fetch_universe()

    assert isinstance(symbols, list)
    assert "RELIANCE" in symbols
    assert "TCS" in symbols
    # BE series must NOT be included
    assert symbols == sorted(symbols)   # sorted ascending
    # No duplicates even though RELIANCE appears twice (EQ + BE)
    assert symbols.count("RELIANCE") == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — fetch_universe() falls back to universe.yaml on HTTP failure
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_universe_fallback_on_http_failure(src: NSEBhavSource) -> None:
    """fetch_universe() returns universe.yaml symbols when bhavcopy fetch fails."""
    with patch.object(src, "_get_zip", side_effect=Exception("network error")):
        symbols = src.fetch_universe()

    # universe.yaml in the fixture has: RELIANCE, TCS, INFY
    assert "RELIANCE" in symbols
    assert "TCS" in symbols
    assert "INFY" in symbols
    assert symbols == sorted(symbols)


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — fetch_single_day() returns exactly one row for a valid trading date
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_single_day_returns_one_row(src: NSEBhavSource) -> None:
    """fetch_single_day() returns a single-row OHLCV DataFrame."""
    zip_data = _single_row_zip(
        symbol="RELIANCE", timestamp="15-JAN-2024",
        open_=100.0, high=110.0, low=95.0, close=105.0, volume=1_000_000,
    )

    with patch.object(src, "_get_zip", return_value=zip_data):
        df = src.fetch_single_day("RELIANCE", trading_date=date(2024, 1, 15))

    assert len(df) == 1
    assert list(df.columns) == list(OHLCV_COLUMNS)
    assert df.index[0] == pd.Timestamp("2024-01-15")
    assert df.iloc[0]["close"] == pytest.approx(105.0)
    assert df.iloc[0]["open"]  == pytest.approx(100.0)
    assert df.iloc[0]["high"]  == pytest.approx(110.0)
    assert df.iloc[0]["low"]   == pytest.approx(95.0)
    assert df.iloc[0]["volume"] == pytest.approx(1_000_000)


# ─────────────────────────────────────────────────────────────────────────────
# Test 10 — fetch_single_day() raises DataFetchError if symbol not in file
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_single_day_raises_if_symbol_missing(src: NSEBhavSource) -> None:
    """fetch_single_day() raises DataFetchError when the symbol is absent."""
    # ZIP only has TCS — asking for RELIANCE should raise
    zip_data = _single_row_zip(symbol="TCS", timestamp="15-JAN-2024")

    with patch.object(src, "_get_zip", return_value=zip_data):
        with pytest.raises(DataFetchError):
            src.fetch_single_day("RELIANCE", trading_date=date(2024, 1, 15))


def test_fetch_single_day_raises_if_file_unavailable(src: NSEBhavSource) -> None:
    """fetch_single_day() raises DataFetchError when bhavcopy file is unavailable (holiday)."""
    with patch.object(src, "_get_zip", return_value=None):
        with pytest.raises(DataFetchError):
            src.fetch_single_day("RELIANCE", trading_date=date(2024, 1, 14))


# ─────────────────────────────────────────────────────────────────────────────
# Test 11 — _build_url() generates the correct URL for a given date
# ─────────────────────────────────────────────────────────────────────────────

def test_build_url_format_standard_date(src: NSEBhavSource) -> None:
    """_build_url() produces the exact NSE archive URL for a mid-month date."""
    url = src._build_url(date(2024, 1, 15))
    assert "2024" in url
    assert "JAN" in url
    assert "cm15JAN2024bhav.csv.zip" in url
    assert url.startswith("https://")


def test_build_url_various_months(src: NSEBhavSource) -> None:
    """_build_url() uses correct uppercase 3-letter month abbreviations."""
    cases = {
        date(2024, 3,  5):  ("MAR", "cm05MAR2024bhav.csv.zip"),
        date(2024, 9,  1):  ("SEP", "cm01SEP2024bhav.csv.zip"),
        date(2024, 12, 31): ("DEC", "cm31DEC2024bhav.csv.zip"),
        date(2023, 6, 15):  ("JUN", "cm15JUN2023bhav.csv.zip"),
    }
    for d, (mmm, filename) in cases.items():
        url = src._build_url(d)
        assert mmm in url, f"Expected {mmm} in URL for {d}"
        assert filename in url, f"Expected {filename} in URL for {d}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 12 — _parse_csv() correctly maps TIMESTAMP → DatetimeIndex, renames cols
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_csv_column_mapping_and_index(src: NSEBhavSource) -> None:
    """_parse_csv() renames NSE columns to canonical names and sets DatetimeIndex."""
    zip_data = _single_row_zip(
        symbol="RELIANCE", timestamp="15-JAN-2024",
        open_=100.0, high=110.0, low=95.0, close=105.0, volume=1_000_000,
    )
    df = src._parse_csv(zip_data, symbol="RELIANCE")

    # Correct columns
    assert list(df.columns) == list(OHLCV_COLUMNS)
    # Correct index type and name
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "date"
    # Correct index value
    assert df.index[0] == pd.Timestamp("2024-01-15")
    # Correct OHLCV values
    assert df.iloc[0]["open"]   == pytest.approx(100.0)
    assert df.iloc[0]["high"]   == pytest.approx(110.0)
    assert df.iloc[0]["low"]    == pytest.approx(95.0)
    assert df.iloc[0]["close"]  == pytest.approx(105.0)
    assert df.iloc[0]["volume"] == pytest.approx(1_000_000)


def test_parse_csv_timestamp_various_dates(src: NSEBhavSource) -> None:
    """_parse_csv() correctly parses DD-MMM-YYYY TIMESTAMP to DatetimeIndex."""
    cases = [
        ("01-JAN-2024", pd.Timestamp("2024-01-01")),
        ("28-FEB-2023", pd.Timestamp("2023-02-28")),
        ("31-DEC-2022", pd.Timestamp("2022-12-31")),
    ]
    for ts_str, expected in cases:
        zip_data = _single_row_zip(symbol="RELIANCE", timestamp=ts_str)
        df = src._parse_csv(zip_data, symbol="RELIANCE")
        assert df.index[0] == expected, f"Failed for timestamp string '{ts_str}'"


def test_parse_csv_no_extra_columns(src: NSEBhavSource) -> None:
    """_parse_csv() returns exactly the 5 canonical OHLCV columns — no extras."""
    zip_data = _single_row_zip(symbol="RELIANCE", timestamp="15-JAN-2024")
    df = src._parse_csv(zip_data, symbol="RELIANCE")
    assert set(df.columns) == set(OHLCV_COLUMNS)


def test_parse_csv_no_symbol_filter_returns_all_eq_rows(src: NSEBhavSource) -> None:
    """_parse_csv(symbol=None) returns all EQ rows, excluding non-EQ series."""
    zip_data = _multi_symbol_zip("15-JAN-2024")
    df = src._parse_csv(zip_data, symbol=None)
    # RELIANCE EQ + TCS EQ = 2 rows; RELIANCE BE must be filtered out
    assert len(df) == 2


def test_parse_csv_returns_empty_if_symbol_absent(src: NSEBhavSource) -> None:
    """_parse_csv() returns an empty DataFrame when the requested symbol is not present."""
    zip_data = _single_row_zip(symbol="TCS", timestamp="15-JAN-2024")
    df = src._parse_csv(zip_data, symbol="RELIANCE")
    assert df.empty
    assert list(df.columns) == list(OHLCV_COLUMNS)


# ─────────────────────────────────────────────────────────────────────────────
# Additional coverage tests
# ─────────────────────────────────────────────────────────────────────────────

def test_name_attribute(src: NSEBhavSource) -> None:
    """NSEBhavSource.name must equal 'nse_bhav'."""
    assert src.name == "nse_bhav"


def test_build_cache_path_structure(src: NSEBhavSource, tmp_path: Path) -> None:
    """_build_cache_path() returns correct hierarchical path."""
    path = src._build_cache_path(date(2024, 1, 15))
    assert "2024" in str(path)
    assert "JAN" in str(path)
    assert path.name == "cm15JAN2024bhav.csv.zip"


def test_fetch_universe_fallback_on_404(src: NSEBhavSource) -> None:
    """fetch_universe() falls back to universe.yaml when today's file is absent (404)."""
    with patch.object(src, "_get_zip", return_value=None):
        symbols = src.fetch_universe()

    assert len(symbols) > 0
    assert "RELIANCE" in symbols


def test_fetch_raises_data_fetch_error_when_all_days_empty(src: NSEBhavSource) -> None:
    """fetch() raises DataFetchError (not InsufficientDataError) when all days return None."""
    with patch.object(src, "_get_zip", return_value=None):
        with pytest.raises(DataFetchError):
            src.fetch("RELIANCE", start=date(2024, 1, 15), end=date(2024, 1, 15))
