"""
utils/exceptions.py
───────────────────
Custom exception hierarchy for the Minervini AI system.

Design rules:
  - Every domain has its own base exception so callers can catch
    at the right level of specificity.
  - All exceptions carry a human-readable message AND optional
    structured context (symbol, date, field, …) so log lines are
    immediately actionable without digging through tracebacks.
  - No exception is ever silently swallowed inside its own module.
    Catch here only to re-raise with richer context.
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────────────────────────

class MinerviniError(Exception):
    """
    Root exception for all project-specific errors.
    Catch this to handle any domain error in a single handler.
    """
    def __init__(self, message: str, **context):
        super().__init__(message)
        self.context: dict = context  # e.g. symbol="DIXON", date="2024-01-15"

    def __str__(self) -> str:
        base = super().__str__()
        if self.context:
            ctx_str = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{base} [{ctx_str}]"
        return base


# ─────────────────────────────────────────────────────────────────────────────
# Configuration errors
# ─────────────────────────────────────────────────────────────────────────────

class ConfigError(MinerviniError):
    """Raised when settings.yaml / universe.yaml is missing or malformed."""


class MissingConfigKeyError(ConfigError):
    """A required config key is absent."""
    def __init__(self, key: str, config_file: str = "settings.yaml"):
        super().__init__(
            f"Required config key '{key}' is missing from {config_file}",
            key=key, config_file=config_file,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Data / ingestion errors
# ─────────────────────────────────────────────────────────────────────────────

class DataError(MinerviniError):
    """Base for all data-layer errors."""


class DataFetchError(DataError):
    """
    Raised when a data source (yfinance, NSE bhavcopy, screener.in)
    fails to return data after all retries.
    """
    def __init__(self, source: str, symbol: str, reason: str = ""):
        super().__init__(
            f"Failed to fetch data from '{source}' for symbol '{symbol}'"
            + (f": {reason}" if reason else ""),
            source=source, symbol=symbol,
        )


class DataValidationError(DataError):
    """
    Raised by ingestion/validator.py when OHLCV data fails a sanity
    check (e.g. high < low, negative volume, missing columns).
    Fail loudly — never silently skip a bad row.
    """
    def __init__(self, symbol: str, field: str, reason: str, row_date: str = ""):
        super().__init__(
            f"Validation failed for '{symbol}'"
            + (f" on {row_date}" if row_date else "")
            + f": field='{field}' — {reason}",
            symbol=symbol, field=field, row_date=row_date,
        )


class InsufficientDataError(DataError):
    """
    Raised when a feature computation requires more rows than are
    available.  Example: SMA_150 needs 150 rows; raise this instead
    of silently returning NaN.
    """
    def __init__(self, symbol: str, required: int, available: int, indicator: str = ""):
        super().__init__(
            f"Insufficient data for '{symbol}': need {required} rows, "
            f"have {available}"
            + (f" (indicator: {indicator})" if indicator else ""),
            symbol=symbol, required=required, available=available,
        )


class UniverseLoadError(DataError):
    """Raised when universe.yaml cannot be parsed or produces an empty list."""


class FundamentalsError(DataError):
    """Base for all Screener.in fundamentals errors."""


class FundamentalsFetchError(FundamentalsError):
    """
    Raised when Screener.in fetch fails after all retries (network error,
    HTTP error, or unrecoverable parse failure).

    Design mandate: NEVER propagate to the caller.  fundamentals failing
    must not crash the pipeline.  Callers catch FundamentalsFetchError
    (or its parent FundamentalsError), log a WARNING, and return None.
    """
    def __init__(self, symbol: str, reason: str = ""):
        super().__init__(
            f"Failed to fetch fundamentals for '{symbol}'"
            + (f": {reason}" if reason else ""),
            symbol=symbol,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist errors
# ─────────────────────────────────────────────────────────────────────────────

class WatchlistError(MinerviniError):
    """Base for watchlist-related errors."""


class WatchlistParseError(WatchlistError):
    """
    Raised when a watchlist file (.csv / .json / .xlsx / .txt) cannot
    be parsed or produces zero valid symbols.
    """
    def __init__(self, path: str, reason: str):
        super().__init__(
            f"Cannot parse watchlist file '{path}': {reason}",
            path=path,
        )


class InvalidSymbolError(WatchlistError):
    """
    Raised when a symbol string fails NSE validation
    (not uppercase alphanumeric, wrong length, etc.).
    """
    def __init__(self, symbol: str, reason: str = ""):
        super().__init__(
            f"Invalid symbol '{symbol}'"
            + (f": {reason}" if reason else ""),
            symbol=symbol,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Feature store errors
# ─────────────────────────────────────────────────────────────────────────────

class FeatureStoreError(MinerviniError):
    """Base for feature-store errors."""


class FeatureStoreOutOfSyncError(FeatureStoreError):
    """
    Raised by feature_store.update() when today's row already exists
    in the feature Parquet file (idempotent guard).
    """
    def __init__(self, symbol: str, run_date: str):
        super().__init__(
            f"Feature store already contains a row for '{symbol}' on {run_date}. "
            "Skipping to avoid duplicate. Use bootstrap() to force a full recompute.",
            symbol=symbol, run_date=run_date,
        )


class FeatureStoreMissingError(FeatureStoreError):
    """
    Raised by feature_store.update() when the feature Parquet file for a
    symbol does not exist.  The caller should run bootstrap() first to
    create the file before calling update().
    """
    def __init__(self, symbol: str):
        super().__init__(
            f"Feature store file for '{symbol}' does not exist. "
            "Run bootstrap() to create it before calling update().",
            symbol=symbol,
        )


class FeatureComputeError(FeatureStoreError):
    """Raised when a feature computation fails unexpectedly."""
    def __init__(self, symbol: str, feature: str, reason: str):
        super().__init__(
            f"Failed to compute feature '{feature}' for '{symbol}': {reason}",
            symbol=symbol, feature=feature,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Rule engine errors
# ─────────────────────────────────────────────────────────────────────────────

class RuleEngineError(MinerviniError):
    """Base for rule-engine errors."""


class ScoringError(RuleEngineError):
    """Raised when scorer.py encounters an unexpected state."""


# ─────────────────────────────────────────────────────────────────────────────
# Storage errors
# ─────────────────────────────────────────────────────────────────────────────

class StorageError(MinerviniError):
    """Base for Parquet / SQLite storage errors."""


class ParquetWriteError(StorageError):
    """Raised when an atomic Parquet write (temp → rename) fails."""
    def __init__(self, path: str, reason: str):
        super().__init__(
            f"Failed to write Parquet file '{path}': {reason}",
            path=path,
        )


class SQLiteError(StorageError):
    """Raised on unexpected SQLite failures (schema mismatch, locked DB, etc.)."""


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline / runner errors
# ─────────────────────────────────────────────────────────────────────────────

class PipelineError(MinerviniError):
    """Base for orchestration / pipeline errors."""


class RunContextError(PipelineError):
    """Raised when RunContext is constructed with invalid parameters."""


# ─────────────────────────────────────────────────────────────────────────────
# LLM errors
# ─────────────────────────────────────────────────────────────────────────────

class LLMError(MinerviniError):
    """
    Base for LLM client errors.
    These must NEVER propagate to the caller — the LLM layer is
    optional.  Catch LLMError → log warning → return None.
    """


class LLMProviderError(LLMError):
    """API call to LLM provider failed (network, auth, rate limit)."""
    def __init__(self, provider: str, reason: str):
        super().__init__(
            f"LLM provider '{provider}' error: {reason}",
            provider=provider,
        )


class LLMResponseError(LLMError):
    """LLM returned a response that could not be parsed."""


# ─────────────────────────────────────────────────────────────────────────────
# Alert errors
# ─────────────────────────────────────────────────────────────────────────────

class AlertError(MinerviniError):
    """
    Base for alerting errors (Telegram, email, webhook).
    Like LLMError, these should never crash the pipeline.
    """


class TelegramAlertError(AlertError):
    """Telegram dispatch failed."""


class EmailAlertError(AlertError):
    """SMTP send failed."""


class WebhookAlertError(AlertError):
    """Generic webhook (Slack / Discord) dispatch failed."""


# ─────────────────────────────────────────────────────────────────────────────
# News errors
# ─────────────────────────────────────────────────────────────────────────────

class NewsError(MinerviniError):
    """Base for all news-layer errors."""


class NewsFetchError(NewsError):
    """
    Raised when an RSS feed fetch fails after all retries.

    Design mandate: NEVER propagate to the caller.
    Callers catch NewsFetchError → log WARNING → skip that feed.
    The pipeline continues with whichever feeds succeeded.
    """
    def __init__(self, feed_url: str, reason: str = ""):
        super().__init__(
            f"Failed to fetch RSS feed '{feed_url}'"
            + (f": {reason}" if reason else ""),
            feed_url=feed_url,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Paper trading errors
# ─────────────────────────────────────────────────────────────────────────────

class PaperTradingError(MinerviniError):
    """
    Base for paper-trading simulator errors.
    Raised by paper_trading/portfolio.py on bad state transitions,
    insufficient cash, or uninitialised portfolio.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Backtest errors
# ─────────────────────────────────────────────────────────────────────────────

class BacktestError(MinerviniError):
    """
    Base for all backtesting errors.
    Raised by backtest/metrics.py, backtest/engine.py, and related modules
    when trade data is malformed, capital is invalid, or computation fails.
    """


class BacktestDataError(BacktestError):
    """Raised when required historical data is missing for the backtest date range."""
    def __init__(self, start_date: str, end_date: str, reason: str):
        super().__init__(
            f"Backtest data unavailable for {start_date} \u2192 {end_date}: {reason}",
            start_date=start_date, end_date=end_date,
        )
