"""
ingestion/nse_bhav.py
─────────────────────
NSE Bhavcopy daily downloader — DataSource implementation.

NSE publishes daily Bhavcopy CSV files as ZIP archives at:
    https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MMM}/
        cm{DD}{MMM}{YYYY}bhav.csv.zip

Example:
    https://archives.nseindia.com/content/historical/EQUITIES/2024/JAN/
        cm15JAN2024bhav.csv.zip

Design:
  - fetch()           : Downloads one ZIP per trading day in [start, end],
                        filters to SERIES=="EQ" and SYMBOL==symbol.
  - fetch_universe()  : Downloads today's Bhavcopy, returns all EQ symbols.
                        Falls back to universe.yaml symbols on failure.
  - fetch_single_day(): Optimised single-day path (one HTTP request, one row).
  - Cache             : Raw ZIPs cached in data/raw/bhav/{YYYY}/{MMM}/
                        If the file exists on disk, the HTTP download is skipped.
  - Retries           : 3 attempts, 2-second wait via tenacity. reraise=True.
  - User-Agent        : NSE sometimes returns 403 for non-Indian IPs without a
                        browser-style User-Agent header.
"""

from __future__ import annotations

import io
import os
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import ClassVar

import pandas as pd
import requests
import yaml
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from ingestion.base import MIN_USABLE_ROWS, OHLCV_COLUMNS, DataSource
from utils.exceptions import DataFetchError, InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "https://archives.nseindia.com/content/historical/EQUITIES/"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_HEADERS = {"User-Agent": _USER_AGENT}

_RETRY_ATTEMPTS = 3
_RETRY_WAIT_SECONDS = 2

# Bhavcopy column → canonical OHLCV column name
_COL_MAP: dict[str, str] = {
    "OPEN":      "open",
    "HIGH":      "high",
    "LOW":       "low",
    "CLOSE":     "close",
    "TOTTRDQTY": "volume",
}

_TIMESTAMP_COL = "TIMESTAMP"   # format in CSV: DD-MMM-YYYY e.g. "15-JAN-2024"
_SYMBOL_COL    = "SYMBOL"
_SERIES_COL    = "SERIES"
_EQ_SERIES     = "EQ"

_DEFAULT_CACHE_DIR     = "data/raw/bhav"
_DEFAULT_UNIVERSE_YAML = "config/universe.yaml"


# ─────────────────────────────────────────────────────────────────────────────
# NSEBhavSource
# ─────────────────────────────────────────────────────────────────────────────

class NSEBhavSource(DataSource):
    """
    NSE Bhavcopy daily data source.

    Covers:
        fetch()            — single-symbol OHLCV download for a date range
        fetch_universe()   — all EQ symbols traded today (source-of-truth)
        fetch_single_day() — optimised path: one HTTP request, one row returned

    All public methods return DataFrames with:
        Index   : tz-naive DatetimeIndex named 'date', sorted ascending
        Columns : open, high, low, close, volume (float64)

    Cache layout:
        data/raw/bhav/{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip
        Cache is append-only — existing files are never deleted or overwritten.
    """

    name: ClassVar[str] = "nse_bhav"

    def __init__(
        self,
        base_url: str | None = None,
        cache_dir: str | Path = _DEFAULT_CACHE_DIR,
        universe_yaml: str | Path = _DEFAULT_UNIVERSE_YAML,
    ) -> None:
        """
        Args:
            base_url:      NSE archive base URL. Reads NSE_BHAV_BASE_URL env var
                           if not supplied; falls back to the default archive URL.
            cache_dir:     Root directory for cached ZIP files.
            universe_yaml: Path to config/universe.yaml — used as fallback when
                           today's Bhavcopy cannot be fetched.
        """
        self._base_url = (
            base_url
            or os.getenv("NSE_BHAV_BASE_URL", _DEFAULT_BASE_URL)
        ).rstrip("/") + "/"
        self._cache_dir = Path(cache_dir)
        self._universe_yaml = Path(universe_yaml)
        log.debug(
            "NSEBhavSource initialised",
            base_url=self._base_url,
            cache_dir=str(self._cache_dir),
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def fetch(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        Download OHLCV for *symbol* over [start, end] (both inclusive).

        Iterates every calendar day in [start, end], downloading one Bhavcopy
        ZIP per day.  Days where the file doesn't exist (weekends, public
        holidays) are silently skipped.

        Args:
            symbol: NSE equity symbol, e.g. "RELIANCE".
            start:  Earliest date (inclusive).
            end:    Latest date (inclusive).

        Returns:
            Cleaned OHLCV DataFrame with DatetimeIndex named 'date', sorted
            ascending.  Only trading days appear in the result.

        Raises:
            DataFetchError:        On persistent HTTP 5xx failure after retries,
                                   or if no data at all was found.
            InsufficientDataError: If result has fewer rows than MIN_USABLE_ROWS
                                   when a full history was requested (bootstrap
                                   mode: date range longer than MIN_USABLE_ROWS
                                   calendar days).
        """
        frames: list[pd.DataFrame] = []
        current = start

        while current <= end:
            try:
                zip_bytes = self._get_zip(current)
            except DataFetchError:
                raise
            except Exception as exc:
                raise DataFetchError(
                    source=self.name,
                    symbol=symbol,
                    reason=f"unexpected error fetching bhavcopy for {current}: {exc}",
                ) from exc

            if zip_bytes is not None:
                df_day = self._parse_csv(zip_bytes, symbol=symbol)
                if not df_day.empty:
                    frames.append(df_day)

            current += timedelta(days=1)

        if not frames:
            raise DataFetchError(
                source=self.name,
                symbol=symbol,
                reason=(
                    f"no data found for '{symbol}' between {start} and {end}. "
                    "Verify the symbol is traded on NSE (series EQ) and the "
                    "date range includes at least one trading day."
                ),
            )

        result = pd.concat(frames).sort_index()

        # Raise InsufficientDataError only in bootstrap mode: long range requested
        # but very few rows returned (e.g. recently-listed symbol).
        days_requested = (end - start).days
        if days_requested > MIN_USABLE_ROWS and len(result) < MIN_USABLE_ROWS:
            raise InsufficientDataError(
                symbol=symbol,
                required=MIN_USABLE_ROWS,
                available=len(result),
            )

        log.debug(
            "fetch complete",
            symbol=symbol,
            start=str(start),
            end=str(end),
            rows=len(result),
        )
        return result

    def fetch_universe(self) -> list[str]:
        """
        Download today's Bhavcopy and return all SERIES=="EQ" symbols.

        This is the source-of-truth for "all NSE equities traded today".
        Falls back to universe.yaml symbols on any fetch or parse failure.

        Returns:
            Sorted list of uppercase NSE equity symbol strings.
        """
        today = date.today()
        try:
            zip_bytes = self._get_zip(today)
            if zip_bytes is None:
                log.warning(
                    "Today's bhavcopy unavailable — falling back to universe.yaml",
                    date=str(today),
                )
                return self._fallback_universe()

            symbols = self._parse_symbols(zip_bytes)
            if not symbols:
                log.warning(
                    "Bhavcopy contained no EQ symbols — falling back",
                    date=str(today),
                )
                return self._fallback_universe()

            log.debug(
                "fetch_universe complete",
                date=str(today),
                symbol_count=len(symbols),
            )
            return symbols

        except Exception as exc:
            log.warning(
                "fetch_universe failed — falling back to universe.yaml",
                date=str(today),
                error=str(exc),
            )
            return self._fallback_universe()

    def fetch_single_day(
        self,
        symbol: str,
        trading_date: date,
    ) -> pd.DataFrame:
        """
        Download only one day's Bhavcopy and return one OHLCV row for *symbol*.

        Overrides the base-class default so the pipeline can use one HTTP
        request instead of fetching a date range.  This is the optimised path
        called by pipeline/runner.py during daily incremental updates.

        Args:
            symbol:       NSE equity symbol.
            trading_date: The exact trading date to fetch.

        Returns:
            Single-row OHLCV DataFrame.

        Raises:
            DataFetchError: If the file is unavailable (holiday/weekend/not yet
                            published) or the symbol is absent from that day's CSV.
        """
        zip_bytes = self._get_zip(trading_date)
        if zip_bytes is None:
            raise DataFetchError(
                source=self.name,
                symbol=symbol,
                reason=(
                    f"no bhavcopy file for {trading_date} — market holiday, "
                    "weekend, or file not yet published by NSE"
                ),
            )

        df = self._parse_csv(zip_bytes, symbol=symbol)
        if df.empty:
            raise DataFetchError(
                source=self.name,
                symbol=symbol,
                reason=(
                    f"symbol '{symbol}' not found in bhavcopy for {trading_date}. "
                    "Verify it is traded on NSE with series EQ."
                ),
            )

        log.debug(
            "fetch_single_day complete",
            symbol=symbol,
            date=str(trading_date),
        )
        return df

    # ── URL helpers ───────────────────────────────────────────────────────────

    def _build_url(self, d: date) -> str:
        """
        Build the NSE Bhavcopy ZIP download URL for the given date.

        Format:
            {base_url}{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip

        Example for 2024-01-15:
            https://archives.nseindia.com/content/historical/EQUITIES/
                2024/JAN/cm15JAN2024bhav.csv.zip
        """
        yyyy = d.strftime("%Y")
        mmm  = d.strftime("%b").upper()   # "Jan" → "JAN"
        dd   = d.strftime("%d")           # zero-padded: "05", "15", "31"
        filename = f"cm{dd}{mmm}{yyyy}bhav.csv.zip"
        return f"{self._base_url}{yyyy}/{mmm}/{filename}"

    def _build_cache_path(self, d: date) -> Path:
        """
        Return the local filesystem cache path for the ZIP of the given date.

        Layout:
            data/raw/bhav/{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip
        """
        yyyy = d.strftime("%Y")
        mmm  = d.strftime("%b").upper()
        dd   = d.strftime("%d")
        filename = f"cm{dd}{mmm}{yyyy}bhav.csv.zip"
        return self._cache_dir / yyyy / mmm / filename

    # ── Download with caching ─────────────────────────────────────────────────

    def _get_zip(self, d: date) -> bytes | None:
        """
        Return raw ZIP bytes for the given date's Bhavcopy.

        Lookup order:
            1. Disk cache — if file exists, read and return it (no HTTP).
            2. HTTP download — fetch from NSE, write to cache on success.

        Returns:
            Raw ZIP bytes, or None if the file doesn't exist on NSE (404).

        Raises:
            DataFetchError: On HTTP 5xx or other non-404 errors after retries.
        """
        cache_path = self._build_cache_path(d)

        # ── Cache hit ────────────────────────────────────────────────────────
        if cache_path.exists():
            log.debug("Cache hit — skipping HTTP", date=str(d), path=str(cache_path))
            return cache_path.read_bytes()

        # ── HTTP download ────────────────────────────────────────────────────
        url = self._build_url(d)
        log.debug("Downloading bhavcopy", date=str(d), url=url)

        try:
            raw = self._download_with_retry(url)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                log.debug("Bhavcopy not found (404) — skipping", date=str(d))
                return None
            raise DataFetchError(
                source=self.name,
                symbol="(universe)",
                reason=(
                    f"HTTP {status} fetching bhavcopy for {d} from {url} "
                    f"after {_RETRY_ATTEMPTS} attempts"
                ),
            ) from exc

        # ── Write to cache (append-only — never overwrite) ───────────────────
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
        log.debug(
            "Cached bhavcopy ZIP",
            date=str(d),
            path=str(cache_path),
            bytes=len(raw),
        )
        return raw

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=wait_fixed(_RETRY_WAIT_SECONDS),
        reraise=True,
    )
    def _download_with_retry(self, url: str) -> bytes:
        """
        HTTP GET with tenacity retry (3 attempts, 2-second fixed wait).

        The @retry decorator retries on ANY exception.  On final failure it
        re-raises so _get_zip can inspect the exception type and convert it
        to an appropriate domain exception (DataFetchError).

        Raises:
            requests.HTTPError:        Non-2xx HTTP response (including 404).
            requests.RequestException: Network-level failure.
        """
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            log.warning(
                "Bhavcopy download failed (will retry)",
                url=url,
                error=str(exc),
            )
            raise

    # ── CSV / symbol parsing ──────────────────────────────────────────────────

    def _parse_csv(
        self,
        zip_bytes: bytes,
        symbol: str | None,
    ) -> pd.DataFrame:
        """
        Parse a Bhavcopy ZIP and return a clean OHLCV DataFrame.

        Steps:
            1. Unzip and read the first .csv file inside.
            2. Strip whitespace from column names.
            3. Filter to rows where SERIES == "EQ".
            4. Optionally filter to SYMBOL == symbol (if not None).
            5. Rename NSE columns to canonical OHLCV names.
            6. Parse TIMESTAMP (DD-MMM-YYYY) → pd.DatetimeIndex named 'date'.
            7. Keep only [open, high, low, close, volume] columns.
            8. Cast all values to float64, sort by date ascending.

        Args:
            zip_bytes: Raw ZIP file bytes.
            symbol:    If given, filter result to this NSE symbol.
                       Pass None to get all EQ rows (used by _parse_symbols).

        Returns:
            Clean OHLCV DataFrame.  Returns an empty DataFrame if the symbol
            is absent or required columns are missing.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                csv_name = next(
                    (n for n in zf.namelist() if n.lower().endswith(".csv")),
                    None,
                )
                if csv_name is None:
                    log.warning("No CSV file found inside Bhavcopy ZIP")
                    return _empty_ohlcv()
                with zf.open(csv_name) as csv_file:
                    df = pd.read_csv(csv_file)
        except Exception as exc:
            log.warning("Failed to unzip/parse Bhavcopy CSV", error=str(exc))
            return _empty_ohlcv()

        # Normalise column names (strip leading/trailing whitespace)
        df.columns = [c.strip() for c in df.columns]

        # Filter to EQ series
        if _SERIES_COL not in df.columns:
            log.warning("SERIES column missing from bhavcopy CSV", columns=list(df.columns))
            return _empty_ohlcv()
        df = df[df[_SERIES_COL].str.strip() == _EQ_SERIES].copy()

        # Filter to specific symbol if requested
        if symbol is not None:
            if _SYMBOL_COL not in df.columns:
                log.warning("SYMBOL column missing from bhavcopy CSV")
                return _empty_ohlcv()
            df = df[df[_SYMBOL_COL].str.strip() == symbol.strip().upper()].copy()

        if df.empty:
            return _empty_ohlcv()

        # Parse TIMESTAMP → DatetimeIndex
        if _TIMESTAMP_COL not in df.columns:
            log.warning("TIMESTAMP column missing from bhavcopy CSV")
            return _empty_ohlcv()

        try:
            df["date"] = pd.to_datetime(
                df[_TIMESTAMP_COL].str.strip(), format="%d-%b-%Y"
            )
        except Exception as exc:
            log.warning("Failed to parse TIMESTAMP column", error=str(exc))
            return _empty_ohlcv()

        df = df.set_index("date")
        df.index = pd.DatetimeIndex(df.index).normalize()
        df.index.name = "date"

        # Rename NSE columns to canonical names
        df = df.rename(columns=_COL_MAP)

        # Verify all OHLCV columns are present
        missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
        if missing:
            log.warning("Missing OHLCV columns after rename", missing=missing)
            return _empty_ohlcv()

        # Keep only the 5 canonical columns, cast to float64
        df = df[list(OHLCV_COLUMNS)].copy()
        for col in OHLCV_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.sort_index()

    def _parse_symbols(self, zip_bytes: bytes) -> list[str]:
        """
        Extract all SERIES=="EQ" symbol names from a Bhavcopy ZIP.

        Only reads SYMBOL and SERIES columns — does not parse OHLCV values.
        Used by fetch_universe() to get the full traded-today symbol list.

        Returns:
            Sorted list of uppercase NSE equity symbol strings.
            Empty list if parsing fails.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                csv_name = next(
                    (n for n in zf.namelist() if n.lower().endswith(".csv")),
                    None,
                )
                if csv_name is None:
                    return []
                with zf.open(csv_name) as csv_file:
                    df = pd.read_csv(csv_file, usecols=[_SYMBOL_COL, _SERIES_COL])
        except Exception as exc:
            log.warning("Failed to parse symbols from bhavcopy ZIP", error=str(exc))
            return []

        df.columns = [c.strip() for c in df.columns]
        if _SERIES_COL not in df.columns or _SYMBOL_COL not in df.columns:
            return []

        eq_mask = df[_SERIES_COL].str.strip() == _EQ_SERIES
        symbols = (
            df.loc[eq_mask, _SYMBOL_COL]
            .str.strip()
            .str.upper()
            .drop_duplicates()
            .tolist()
        )
        return sorted(symbols)

    # ── Fallback universe ─────────────────────────────────────────────────────

    def _fallback_universe(self) -> list[str]:
        """
        Return symbols from universe.yaml as a fallback when today's
        Bhavcopy cannot be fetched or parsed.

        Returns:
            Sorted list of uppercase NSE symbol strings.
            Empty list if universe.yaml is missing or unreadable.
        """
        try:
            if not self._universe_yaml.exists():
                log.warning(
                    "universe.yaml not found for fallback",
                    path=str(self._universe_yaml),
                )
                return []
            with self._universe_yaml.open() as fh:
                config = yaml.safe_load(fh) or {}
            raw: list = config.get("symbols", []) or []
            symbols = sorted(
                str(s).strip().upper()
                for s in raw
                if s and str(s).strip()
            )
            log.info(
                "Fallback universe loaded from universe.yaml",
                symbol_count=len(symbols),
                path=str(self._universe_yaml),
            )
            return symbols
        except Exception as exc:
            log.error("Failed to load fallback universe", error=str(exc), exc_info=True)
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _empty_ohlcv() -> pd.DataFrame:
    """
    Return an empty DataFrame with the canonical OHLCV schema.

    Used as a safe sentinel return value from _parse_csv() when the CSV
    contains no relevant rows (symbol not found, wrong series, etc.).
    """
    df = pd.DataFrame(columns=list(OHLCV_COLUMNS))
    df.index = pd.DatetimeIndex([], name="date")
    return df
