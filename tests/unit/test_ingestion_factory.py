"""
tests/unit/test_ingestion_factory.py
─────────────────────────────────────
Unit tests for ingestion.get_data_source() factory.

Spec (from task brief):
    1. get_data_source({"universe": {"source": "yfinance"}})  → YFinanceSource
    2. get_data_source({"universe": {"source": "nse_bhav"}})  → NSEBhavSource
    3. get_data_source({"universe": {"source": "csv"}})       → raises ConfigurationError
    4. Missing key defaults to YFinanceSource.
    5. ConfigurationError message names the bad value and the valid options.

All DataSource constructors are patched so no filesystem or network access
occurs during tests (YFinanceSource reads universe.yaml on __init__,
NSEBhavSource writes no files but we patch for symmetry and speed).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ingestion import get_data_source
from ingestion.nse_bhav import NSEBhavSource
from ingestion.yfinance_source import YFinanceSource
from utils.exceptions import ConfigurationError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(source: str) -> dict:
    """Build a minimal config dict with the given universe.source value."""
    return {"universe": {"source": source}}


# ─────────────────────────────────────────────────────────────────────────────
# 1. source = "yfinance" → YFinanceSource
# ─────────────────────────────────────────────────────────────────────────────

def test_yfinance_source_returns_yfinance_instance() -> None:
    """get_data_source with source='yfinance' returns a YFinanceSource."""
    with patch.object(YFinanceSource, "__init__", return_value=None):
        result = get_data_source(_cfg("yfinance"))
    assert isinstance(result, YFinanceSource)


# ─────────────────────────────────────────────────────────────────────────────
# 2. source = "nse_bhav" → NSEBhavSource
# ─────────────────────────────────────────────────────────────────────────────

def test_nse_bhav_source_returns_nse_bhav_instance() -> None:
    """get_data_source with source='nse_bhav' returns an NSEBhavSource."""
    with patch.object(NSEBhavSource, "__init__", return_value=None):
        result = get_data_source(_cfg("nse_bhav"))
    assert isinstance(result, NSEBhavSource)


# ─────────────────────────────────────────────────────────────────────────────
# 3. source = "csv" → raises ConfigurationError
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_source_raises_configuration_error() -> None:
    """get_data_source raises ConfigurationError for unrecognised source values."""
    with pytest.raises(ConfigurationError):
        get_data_source(_cfg("csv"))


def test_configuration_error_message_names_bad_value() -> None:
    """ConfigurationError message contains the offending value."""
    with pytest.raises(ConfigurationError, match="csv"):
        get_data_source(_cfg("csv"))


def test_configuration_error_message_names_valid_options() -> None:
    """ConfigurationError message mentions the valid source names."""
    with pytest.raises(ConfigurationError, match="yfinance"):
        get_data_source(_cfg("csv"))
    with pytest.raises(ConfigurationError, match="nse_bhav"):
        get_data_source(_cfg("bloomberg"))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Missing universe.source key defaults to YFinanceSource
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_source_key_defaults_to_yfinance() -> None:
    """When universe.source is absent the factory defaults to YFinanceSource."""
    with patch.object(YFinanceSource, "__init__", return_value=None):
        result = get_data_source({})               # empty config
    assert isinstance(result, YFinanceSource)


def test_missing_universe_section_defaults_to_yfinance() -> None:
    """When universe section is missing entirely the factory defaults to YFinanceSource."""
    with patch.object(YFinanceSource, "__init__", return_value=None):
        result = get_data_source({"data": {"raw_dir": "data/raw"}})
    assert isinstance(result, YFinanceSource)


# ─────────────────────────────────────────────────────────────────────────────
# 5. ConfigurationError carries structured context attributes
# ─────────────────────────────────────────────────────────────────────────────

def test_configuration_error_has_key_attribute() -> None:
    """ConfigurationError.context contains the 'key' field for the bad config path."""
    with pytest.raises(ConfigurationError) as exc_info:
        get_data_source(_cfg("csv"))
    assert exc_info.value.context.get("key") == "universe.source"


def test_configuration_error_has_value_attribute() -> None:
    """ConfigurationError.context contains the 'value' field with the bad string."""
    with pytest.raises(ConfigurationError) as exc_info:
        get_data_source(_cfg("bloomberg"))
    assert exc_info.value.context.get("value") == "bloomberg"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Additional bad source strings all raise ConfigurationError
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_source", [
    "csv",
    "bloomberg",
    "zerodha",
    "YFINANCE",   # wrong case
    "nse_bhav2",  # typo
    "",           # empty string
])
def test_bad_sources_all_raise_configuration_error(bad_source: str) -> None:
    """Every unrecognised source string raises ConfigurationError."""
    with pytest.raises(ConfigurationError):
        get_data_source(_cfg(bad_source))


# ─────────────────────────────────────────────────────────────────────────────
# 7. Returned objects implement the DataSource interface
# ─────────────────────────────────────────────────────────────────────────────

def test_yfinance_result_has_fetch_method() -> None:
    """The YFinanceSource returned by the factory has a fetch() method."""
    from ingestion.base import DataSource
    with patch.object(YFinanceSource, "__init__", return_value=None):
        result = get_data_source(_cfg("yfinance"))
    assert isinstance(result, DataSource)
    assert callable(getattr(result, "fetch", None))


def test_nse_bhav_result_has_fetch_method() -> None:
    """The NSEBhavSource returned by the factory has a fetch() method."""
    from ingestion.base import DataSource
    with patch.object(NSEBhavSource, "__init__", return_value=None):
        result = get_data_source(_cfg("nse_bhav"))
    assert isinstance(result, DataSource)
    assert callable(getattr(result, "fetch", None))
