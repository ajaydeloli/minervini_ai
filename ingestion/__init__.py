"""
ingestion/__init__.py
─────────────────────
Re-exports the complete public API of the ingestion package so callers
can write flat imports rather than dotted submodule paths.

    # Abstract interface + constants
    from ingestion import DataSource, OHLCV_COLUMNS, MIN_USABLE_ROWS
    from ingestion import BENCHMARK_PRIMARY, BENCHMARK_FALLBACK

    # Concrete data sources + factory
    from ingestion import YFinanceSource, NSEBhavSource
    from ingestion import get_data_source   # preferred — reads universe.source from config

    # Validation + cleaning
    from ingestion import validate, check_sufficient_history, detect_gaps

    # Universe / watchlist resolution
    from ingestion import RunSymbols, resolve_symbols
    from ingestion import load_universe_yaml, load_watchlist_file, validate_symbol

Submodule responsibilities:
    base.py             — DataSource ABC, OHLCV_COLUMNS, benchmark constants
    yfinance_source.py  — YFinanceSource (fetch, fetch_benchmark, fetch_ohlcv_bulk)
    nse_bhav.py         — NSEBhavSource  (fetch, fetch_universe, fetch_single_day)
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

# ── nse_bhav ──────────────────────────────────────────────────────────────────
from ingestion.nse_bhav import NSEBhavSource

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


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def get_data_source(config: dict) -> DataSource:
    """
    Instantiate and return the DataSource implementation specified by
    ``config["universe"]["source"]``.

    Supported values
    ────────────────
        "yfinance"  → YFinanceSource  (default when key is absent)
        "nse_bhav"  → NSEBhavSource

    All other values raise ConfigurationError so misconfiguration is caught
    immediately at startup rather than silently falling back to the wrong source.

    Args:
        config: Application configuration dict loaded from settings.yaml.

    Returns:
        A concrete DataSource instance ready for use.

    Raises:
        ConfigurationError: When universe.source holds an unrecognised value
                            (e.g. "csv", "bloomberg", or a typo).

    Example::

        from ingestion import get_data_source
        import yaml

        config = yaml.safe_load(open("config/settings.yaml"))
        src = get_data_source(config)          # respects universe.source
        df  = src.fetch("RELIANCE", start, end)
    """
    source: str = config.get("universe", {}).get("source", "yfinance")

    if source == "yfinance":
        return YFinanceSource()

    if source == "nse_bhav":
        return NSEBhavSource()

    from utils.exceptions import ConfigurationError
    raise ConfigurationError(
        f"Unknown data source '{source}' in universe.source. "
        "Valid values: 'yfinance', 'nse_bhav'.",
        key="universe.source",
        value=source,
    )

__all__ = [
    # ── base: abstract interface + constants ──────────────────────────────────
    "DataSource",
    "OHLCV_COLUMNS",
    "MIN_USABLE_ROWS",
    "BENCHMARK_PRIMARY",
    "BENCHMARK_FALLBACK",
    # ── data source implementations + factory ─────────────────────────────────
    "YFinanceSource",
    "NSEBhavSource",
    "get_data_source",
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
