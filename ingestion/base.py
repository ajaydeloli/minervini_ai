"""
ingestion/base.py
─────────────────
Abstract DataSource interface for all market data providers.

Design mandate (PROJECT_DESIGN.md §6.1):
    All data sources implement this interface.  New data providers
    (Zerodha, Breeze, NSE Bhavcopy, etc.) require only a new adapter
    class — zero changes to pipeline logic.

Contract:
    fetch()          → Returns a clean DatetimeIndex DataFrame with columns
                       [open, high, low, close, volume].  The caller receives
                       raw (unadjusted) prices exactly as the provider returns
                       them; adjustment for splits/bonuses happens in the
                       validator/processor layer.

    fetch_universe() → Returns the list of symbols this provider knows about.
                       For yfinance this is the symbols in universe.yaml;
                       for NSE Bhavcopy it is all equity symbols traded that day.

    fetch_benchmark() → Returns OHLCV for the benchmark index used to compute
                        RS Rating (Nifty 500 TRI — ^CRSLDX, fallback ^NSEI).
                        DataSource implementors may raise NotImplementedError
                        if they cannot supply benchmark data.

Error handling:
    All network/parsing errors must be raised as DataFetchError (never
    silently swallowed).  InsufficientDataError is raised when the
    provider returns fewer bars than the caller requested.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import ClassVar

import pandas as pd

from utils.exceptions import DataFetchError, InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Column name constants — every DataSource must return exactly these columns
# ─────────────────────────────────────────────────────────────────────────────

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

# Minimum number of rows a fetch() call must return for it to be usable.
# SMA_200 needs 200 rows; we add a 50-row buffer for safety.
MIN_USABLE_ROWS: int = 250


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base class
# ─────────────────────────────────────────────────────────────────────────────

class DataSource(ABC):
    """
    Abstract base class for all market data providers.

    Implementors must override:
        fetch()           — download OHLCV for one symbol over a date range
        fetch_universe()  — return list of symbols this source covers

    Implementors may override:
        fetch_benchmark() — return OHLCV for the RS benchmark index
        name              — human-readable provider name (class variable)

    All public methods on this class log at DEBUG level so callers can
    trace data lineage without adding their own log statements.
    """

    # Subclasses should set this to a short provider identifier, e.g. "yfinance"
    name: ClassVar[str] = "unknown"

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        Download OHLCV data for *symbol* over [start, end] (both inclusive).

        Args:
            symbol: NSE symbol string (e.g. "RELIANCE", "TCS").
                    The implementation is responsible for any ticker
                    suffix required by the provider (e.g. ".NS" for yfinance).
            start:  Earliest date to include in the result.
            end:    Latest date to include in the result.

        Returns:
            DataFrame with:
                Index : pd.DatetimeIndex named "date", sorted ascending,
                        containing only trading days in [start, end].
                Columns: open, high, low, close, volume  (all float64 except
                         volume which may be int64 or float64).
            The returned data is raw / unadjusted unless the provider only
            supplies adjusted prices (yfinance default).

        Raises:
            DataFetchError       : On network failure, auth error, or empty
                                   response after retries.
            InsufficientDataError: If the provider returns fewer rows than
                                   MIN_USABLE_ROWS when a full history was
                                   requested (bootstrap mode).
        """
        ...

    @abstractmethod
    def fetch_universe(self) -> list[str]:
        """
        Return the list of all tradeable symbols this data source covers.

        For providers backed by a config file (yfinance + universe.yaml),
        this returns the symbols from that file.  For exchange-feed providers
        (NSE Bhavcopy), this returns every equity symbol traded that day.

        Returns:
            Sorted list of NSE symbol strings (uppercase, no suffix).

        Raises:
            UniverseLoadError: If the symbol list cannot be determined.
        """
        ...

    # ── Concrete methods with default implementations ────────────────────────

    def fetch_benchmark(
        self,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        Download OHLCV for the RS Rating benchmark index.

        Default implementation raises NotImplementedError.  Override in
        providers that can supply index data (e.g. YFinanceSource).

        The benchmark used for Minervini RS Rating is the Nifty 500 TRI
        (^CRSLDX).  If unavailable, fall back to ^NSEI (Nifty 50).

        Args:
            start: Earliest date.
            end:   Latest date.

        Returns:
            OHLCV DataFrame with the same shape/index contract as fetch().

        Raises:
            NotImplementedError: If this provider cannot supply benchmark data.
            DataFetchError:      On network failure.
        """
        raise NotImplementedError(
            f"DataSource '{self.name}' does not implement fetch_benchmark(). "
            "Use YFinanceSource for benchmark data."
        )

    def fetch_single_day(
        self,
        symbol: str,
        trading_date: date,
    ) -> pd.DataFrame:
        """
        Convenience wrapper: fetch exactly one trading day's OHLCV.

        Uses fetch(symbol, trading_date, trading_date) internally.
        Returns a single-row DataFrame or raises DataFetchError if the
        provider returns no data for that date (e.g. market holiday).

        Args:
            symbol:       NSE symbol.
            trading_date: The exact date to fetch.

        Returns:
            Single-row DataFrame.  Raises DataFetchError if empty.
        """
        df = self.fetch(symbol, start=trading_date, end=trading_date)
        if df.empty:
            raise DataFetchError(
                source=self.name,
                symbol=symbol,
                reason=f"no data returned for {trading_date} (market holiday or delisted?)",
            )
        return df

    def validate_response(
        self,
        df: pd.DataFrame,
        symbol: str,
        min_rows: int = 1,
    ) -> None:
        """
        Lightweight structural check called inside fetch() implementations
        before returning to the caller.

        Verifies:
            1. DataFrame is not empty.
            2. All required OHLCV columns are present.
            3. At least *min_rows* rows are present.

        Args:
            df:       The raw DataFrame from the provider.
            symbol:   Symbol name for error context.
            min_rows: Minimum acceptable row count (default 1).

        Raises:
            DataFetchError:       If df is empty or missing columns.
            InsufficientDataError: If len(df) < min_rows.
        """
        if df is None or df.empty:
            raise DataFetchError(
                source=self.name,
                symbol=symbol,
                reason="provider returned empty DataFrame",
            )

        missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
        if missing:
            raise DataFetchError(
                source=self.name,
                symbol=symbol,
                reason=f"missing columns: {missing}",
            )

        if len(df) < min_rows:
            raise InsufficientDataError(
                symbol=symbol,
                required=min_rows,
                available=len(df),
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark ticker constants (used by YFinanceSource + RS feature)
# ─────────────────────────────────────────────────────────────────────────────

BENCHMARK_PRIMARY   = "^CRSLDX"   # Nifty 500 TRI — preferred
BENCHMARK_FALLBACK  = "^NSEI"     # Nifty 50    — fallback if TRI unavailable
