"""
tests/unit/test_llm_explainer.py
──────────────────────────────────
Unit tests for llm/explainer.py.

All LLM calls are mocked via unittest.mock.patch so no real API traffic
is generated.  SEPAResult objects are constructed with the minimal fields
required to exercise each code path.
"""

from __future__ import annotations

import datetime
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from llm.explainer import generate_trade_brief, generate_watchlist_summary
from rules.scorer import SEPAResult
from utils.exceptions import LLMProviderError


# ─────────────────────────────────────────────────────────────────────────────
# Configs
# ─────────────────────────────────────────────────────────────────────────────

_LLM_DISABLED_CONFIG = {"llm": {"enabled": False}}

_LLM_ENABLED_CONFIG = {
    "llm": {
        "enabled": True,
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "max_tokens": 350,
        "only_for_quality": ["A+", "A"],
    }
}

_RUN_DATE = datetime.date(2025, 1, 15)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_result(symbol: str = "DIXON", setup_quality: str = "A+", score: int = 91) -> SEPAResult:
    """Return a minimal but valid SEPAResult for testing."""
    return SEPAResult(
        symbol=symbol,
        date=datetime.date(2025, 1, 15),
        stage=2,
        stage_label="Stage 2 — Advancing",
        stage_confidence=85,
        trend_template_pass=True,
        trend_template_details={},
        conditions_met=8,
        vcp_qualified=True,
        vcp_grade="A",
        vcp_details={"contraction_count": 3, "final_depth_pct": 5.2, "vol_ratio": 0.3},
        breakout_triggered=True,
        entry_price=4500.0,
        stop_loss=4200.0,
        stop_type="vcp",
        risk_pct=6.7,
        rs_rating=88,
        rr_ratio=2.5,
        target_price=5250.0,
        reward_pct=16.7,
        has_resistance=True,
        fundamental_pass=True,
        fundamental_details={
            "conditions_met": 6,
            "roe": 22.5,
            "debt_to_equity": 0.4,
            "eps_accelerating": True,
            "sales_growth_yoy": 18.0,
            "promoter_holding": 45.0,
        },
        news_score=35.0,
        setup_quality=setup_quality,
        score=score,
    )


def _make_ohlcv(rows: int = 60) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with *rows* rows."""
    dates = pd.date_range("2024-11-01", periods=rows, freq="B")
    return pd.DataFrame(
        {
            "close": np.linspace(3800, 4500, rows),
            "high": np.linspace(3900, 4600, rows),
            "low": np.linspace(3700, 4400, rows),
            "open": np.linspace(3850, 4480, rows),
            "volume": np.ones(rows) * 1_000_000,
        },
        index=dates,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TestGenerateTradeBriefDisabled
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateTradeBriefDisabled(unittest.TestCase):

    @patch("llm.explainer.get_llm_client")
    def test_llm_disabled_returns_none(self, mock_get_client):
        """When LLM is disabled in config, generate_trade_brief returns None without calling get_llm_client."""
        result = generate_trade_brief(_make_result(), _make_ohlcv(), _LLM_DISABLED_CONFIG)

        self.assertIsNone(result)
        mock_get_client.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# TestGenerateTradeBriefQualityFilter
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateTradeBriefQualityFilter(unittest.TestCase):

    @patch("llm.explainer.get_llm_client")
    def test_b_quality_filtered_out(self, mock_get_client):
        """A 'B' quality result is silently filtered out; function returns None."""
        result = generate_trade_brief(
            _make_result(setup_quality="B"),
            _make_ohlcv(),
            _LLM_ENABLED_CONFIG,
        )

        self.assertIsNone(result)

    @patch("llm.explainer.get_llm_client")
    def test_a_plus_not_filtered(self, mock_get_client):
        """An 'A+' quality result passes the filter and returns the LLM string."""
        mock_client = MagicMock()
        mock_client.complete.return_value = "DIXON breakout confirmed."
        mock_get_client.return_value = mock_client

        result = generate_trade_brief(
            _make_result(setup_quality="A+"),
            _make_ohlcv(),
            _LLM_ENABLED_CONFIG,
        )

        self.assertIsNotNone(result)


# ─────────────────────────────────────────────────────────────────────────────
# TestGenerateTradeBriefSuccess
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateTradeBriefSuccess(unittest.TestCase):

    @patch("llm.explainer.get_llm_client")
    def test_returns_stripped_string(self, mock_get_client):
        """LLM response with surrounding whitespace is returned fully stripped."""
        mock_client = MagicMock()
        mock_client.complete.return_value = "  DIXON breakout confirmed.  "
        mock_get_client.return_value = mock_client

        result = generate_trade_brief(_make_result(), _make_ohlcv(), _LLM_ENABLED_CONFIG)

        self.assertEqual(result, "DIXON breakout confirmed.")

    @patch("llm.explainer.get_llm_client")
    def test_result_contains_symbol(self, mock_get_client):
        """Returned string matches exactly what the mocked LLM client returned (after strip)."""
        expected = "DIXON: Stage 2 breakout on high volume."
        mock_client = MagicMock()
        mock_client.complete.return_value = expected
        mock_get_client.return_value = mock_client

        result = generate_trade_brief(_make_result(), _make_ohlcv(), _LLM_ENABLED_CONFIG)

        self.assertTrue(result)
        self.assertEqual(result, expected)


# ─────────────────────────────────────────────────────────────────────────────
# TestGenerateTradeBriefErrorHandling
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateTradeBriefErrorHandling(unittest.TestCase):

    @patch("llm.explainer.get_llm_client")
    def test_llm_provider_error_returns_none(self, mock_get_client):
        """When client.complete() raises LLMProviderError, function returns None without re-raising."""
        mock_client = MagicMock()
        mock_client.complete.side_effect = LLMProviderError("groq", "rate limit hit")
        mock_get_client.return_value = mock_client

        result = generate_trade_brief(_make_result(), _make_ohlcv(), _LLM_ENABLED_CONFIG)

        self.assertIsNone(result)

    @patch("llm.explainer.get_llm_client")
    def test_generic_exception_returns_none(self, mock_get_client):
        """When client.complete() raises a generic Exception, function returns None without re-raising."""
        mock_client = MagicMock()
        mock_client.complete.side_effect = Exception("unexpected")
        mock_get_client.return_value = mock_client

        result = generate_trade_brief(_make_result(), _make_ohlcv(), _LLM_ENABLED_CONFIG)

        self.assertIsNone(result)

    @patch("llm.explainer.get_llm_client")
    def test_empty_ohlcv_returns_none(self, mock_get_client):
        """Passing an empty DataFrame as ohlcv_tail causes function to return None gracefully."""
        result = generate_trade_brief(_make_result(), pd.DataFrame(), _LLM_ENABLED_CONFIG)

        self.assertIsNone(result)

    @patch("llm.explainer.get_llm_client")
    def test_get_llm_client_returns_none(self, mock_get_client):
        """When get_llm_client returns None (bad config), function returns None."""
        mock_get_client.return_value = None

        result = generate_trade_brief(_make_result(), _make_ohlcv(), _LLM_ENABLED_CONFIG)

        self.assertIsNone(result)


# ─────────────────────────────────────────────────────────────────────────────
# TestGenerateWatchlistSummary
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateWatchlistSummary(unittest.TestCase):

    @patch("llm.explainer.get_llm_client")
    def test_empty_results_returns_none(self, mock_get_client):
        """generate_watchlist_summary with an empty results list returns None."""
        result = generate_watchlist_summary([], _RUN_DATE, _LLM_ENABLED_CONFIG)

        self.assertIsNone(result)

    @patch("llm.explainer.get_llm_client")
    def test_disabled_returns_none(self, mock_get_client):
        """generate_watchlist_summary with LLM disabled in config returns None."""
        results = [_make_result("DIXON", "A+", 91), _make_result("RELIANCE", "A", 75)]

        result = generate_watchlist_summary(results, _RUN_DATE, _LLM_DISABLED_CONFIG)

        self.assertIsNone(result)
        mock_get_client.assert_not_called()

    @patch("llm.explainer.get_llm_client")
    def test_success_returns_string(self, mock_get_client):
        """When the LLM returns a summary string, that string is returned (stripped)."""
        expected = "Today's screen found 3 setups."
        mock_client = MagicMock()
        mock_client.complete.return_value = expected
        mock_get_client.return_value = mock_client

        results = [
            _make_result("DIXON", "A+", 91),
            _make_result("RELIANCE", "A", 75),
            _make_result("ZOMATO", "B", 58),
        ]
        result = generate_watchlist_summary(results, _RUN_DATE, _LLM_ENABLED_CONFIG)

        self.assertEqual(result, expected)

    @patch("llm.explainer.get_llm_client")
    def test_llm_error_returns_none(self, mock_get_client):
        """When the LLM raises LLMProviderError during summary, function returns None."""
        mock_client = MagicMock()
        mock_client.complete.side_effect = LLMProviderError("groq", "quota exceeded")
        mock_get_client.return_value = mock_client

        results = [_make_result("DIXON", "A+", 91)]
        result = generate_watchlist_summary(results, _RUN_DATE, _LLM_ENABLED_CONFIG)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
