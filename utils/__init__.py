"""
utils/__init__.py
─────────────────
Convenience re-exports for the utils package.

Import the most-used symbols at the package level so callers can write:

    from utils import get_logger, MinerviniError, is_trading_day

instead of the full dotted path.  Keep this list short — only what is
needed in almost every module.  Exotic helpers are imported directly
from their submodule.
"""

from utils.exceptions import (
    # Root
    MinerviniError,
    # Config
    ConfigError,
    MissingConfigKeyError,
    # Data / ingestion
    DataError,
    DataFetchError,
    DataValidationError,
    InsufficientDataError,
    UniverseLoadError,
    # Watchlist
    WatchlistError,
    WatchlistParseError,
    InvalidSymbolError,
    # Feature store
    FeatureStoreError,
    FeatureStoreOutOfSyncError,
    FeatureComputeError,
    # Rule engine
    RuleEngineError,
    ScoringError,
    # Storage
    StorageError,
    ParquetWriteError,
    SQLiteError,
    # Pipeline
    PipelineError,
    RunContextError,
    # LLM
    LLMError,
    LLMProviderError,
    LLMResponseError,
    # Alerts
    AlertError,
    TelegramAlertError,
    EmailAlertError,
)

from utils.logger import get_logger, setup_logging
from utils.env_check import warn_missing_env_vars

from utils.date_utils import (
    is_trading_day,
    prev_trading_day,
    next_trading_day,
    trading_days_between,
    count_trading_days,
    market_is_open,
    ist_now,
    today_ist,
    parse_date,
    format_date,
    trading_days_ago,
    required_history_start,
    last_n_trading_days,
    IST,
)

from utils.math_utils import (
    linear_slope,
    normalised_slope,
    percentile_rank,
    pct_change,
    pct_above,
    pct_below_high,
    depth_pct,
    is_contracting,
    weighted_score,
    safe_divide,
    clamp,
    round2,
    is_finite,
    true_range,
    average_true_range,
    rolling_mean,
    rolling_max,
    rolling_min,
)

__all__ = [
    # exceptions
    "MinerviniError", "ConfigError", "MissingConfigKeyError",
    "DataError", "DataFetchError", "DataValidationError",
    "InsufficientDataError", "UniverseLoadError",
    "WatchlistError", "WatchlistParseError", "InvalidSymbolError",
    "FeatureStoreError", "FeatureStoreOutOfSyncError", "FeatureComputeError",
    "RuleEngineError", "ScoringError",
    "StorageError", "ParquetWriteError", "SQLiteError",
    "PipelineError", "RunContextError",
    "LLMError", "LLMProviderError", "LLMResponseError",
    "AlertError", "TelegramAlertError", "EmailAlertError",
    # logger
    "get_logger", "setup_logging",
    # env_check
    "warn_missing_env_vars",
    # date_utils
    "is_trading_day", "prev_trading_day", "next_trading_day",
    "trading_days_between", "count_trading_days",
    "market_is_open", "ist_now", "today_ist",
    "parse_date", "format_date", "trading_days_ago",
    "required_history_start", "last_n_trading_days", "IST",
    # math_utils
    "linear_slope", "normalised_slope", "percentile_rank",
    "pct_change", "pct_above", "pct_below_high",
    "depth_pct", "is_contracting", "weighted_score",
    "safe_divide", "clamp", "round2", "is_finite",
    "true_range", "average_true_range",
    "rolling_mean", "rolling_max", "rolling_min",
]
