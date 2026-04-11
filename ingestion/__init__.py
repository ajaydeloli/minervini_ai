"""
ingestion/__init__.py
─────────────────────
Re-exports the complete public API of the ingestion package so callers
can write flat imports rather than dotted submodule paths.

    # Abstract interface + constants
    from ingestion import DataSource, OHLCV_COLUMNS, MIN_USABLE_ROWS
    from ingestion import BENCHMARK_PRIMARY, BENCHMARK_FALLBACK

    # Concrete data source
    from ingestion import YFinanceSource

    # Validation + cleaning
    from ingestion import validate, check_sufficient_history, detect_gaps

    # Universe / watchlist resolution
    from ingestion import RunSymbols, resolve_symbols
    from ingestion import load_universe_yaml, load_watchlist_file, validate_symbol

Submodule responsibilities:
    base.py             — DataSource ABC, OHLCV_COLUMNS, benchmark constants
    yfinance_source.py  — YFinanceSource (fetch, fetch_benchmark, fetch_ohlcv_bulk)
    validator.py        — validate, check_sufficient_history, detect_gaps
    universe_loader.py  — RunSymbols, resolve_symbols, load_watchlist_file,
                          validate_symbol, load_universe_yaml
"""

# ── base ──────────────────────────────────────────────────────────────────────
from ingestion.base import (
    BENCHMARK_FALLBACK,
    BENCHMARK_PRIMARY,
    MIN_USABLE_ROWS,
    OHLCV_COLUMNS,
    DataSource,
)

# ── yfinance_source ───────────────────────────────────────────────────────────
from ingestion.yfinance_source import YFinanceSource

# ── validator ─────────────────────────────────────────────────────────────────
from ingestion.validator import (
    check_sufficient_history,
    detect_gaps,
    validate,
)

# ── universe_loader ───────────────────────────────────────────────────────────
from ingestion.universe_loader import (
    RunSymbols,
    load_universe_yaml,
    load_watchlist_file,
    resolve_symbols,
    validate_symbol,
)

# ── fundamentals ──────────────────────────────────────────────────────────────
from ingestion.fundamentals import (
    fetch_fundamentals,
    is_cache_valid,
)

__all__ = [
    # ── base: abstract interface + constants ──────────────────────────────────
    "DataSource",
    "OHLCV_COLUMNS",
    "MIN_USABLE_ROWS",
    "BENCHMARK_PRIMARY",
    "BENCHMARK_FALLBACK",
    # ── yfinance_source: concrete provider ────────────────────────────────────
    "YFinanceSource",
    # ── validator: OHLCV cleaning and checks ──────────────────────────────────
    "validate",
    "check_sufficient_history",
    "detect_gaps",
    # ── universe_loader: symbol resolution ────────────────────────────────────
    "RunSymbols",
    "resolve_symbols",
    "load_universe_yaml",
    "load_watchlist_file",
    "validate_symbol",
    # ── fundamentals: Screener.in scraper + cache ─────────────────────────────
    "fetch_fundamentals",
    "is_cache_valid",
]
