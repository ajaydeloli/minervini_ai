"""
tests/unit/test_telegram_alert.py
──────────────────────────────────
Unit tests for alerts/telegram_alert.py.

All HTTP calls are mocked via unittest.mock.patch so no real network
traffic is generated.  SEPAResult objects are constructed with the
minimal fields required to exercise each code path.
"""

from __future__ import annotations

import datetime
import unittest
from unittest.mock import MagicMock, patch

import requests

from alerts.telegram_alert import TelegramAlert
from alerts.base import AlertResult
from rules.scorer import SEPAResult
from utils.exceptions import TelegramAlertError


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_RUN_DATE = datetime.date(2025, 1, 15)

_BASE_CONFIG = {
    "alerts": {
        "telegram": {
            "enabled": True,
            "min_quality": "A",
        }
    },
    "watchlist": {
        "min_score_alert": 55,
    },
}

_DISABLED_CONFIG = {
    "alerts": {"telegram": {"enabled": False}},
    "watchlist": {"min_score_alert": 55},
}


def _make_result(
    symbol: str = "DIXON",
    setup_quality: str = "A",
    score: int = 75,
    stage_label: str = "Stage 2",
    rs_rating: int = 85,
    entry_price: float = 4500.0,
    stop_loss: float = 4230.0,
    risk_pct: float = 6.0,
    vcp_qualified: bool = True,
    breakout_triggered: bool = True,
) -> SEPAResult:
    """Return a minimal but valid SEPAResult for testing."""
    return SEPAResult(
        symbol=symbol,
        date=_RUN_DATE,
        stage=2,
        stage_label=stage_label,
        stage_confidence=80,
        trend_template_pass=True,
        trend_template_details={},
        conditions_met=8,
        vcp_qualified=vcp_qualified,
        vcp_grade="A",
        vcp_details={},
        breakout_triggered=breakout_triggered,
        entry_price=entry_price,
        stop_loss=stop_loss,
        stop_type="atr",
        risk_pct=risk_pct,
        rs_rating=rs_rating,
        setup_quality=setup_quality,
        score=score,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────────────────

class TestTelegramAlertDisabled(unittest.TestCase):

    def test_disabled_returns_zero_sent(self):
        """When enabled=False, send() must return immediately with sent=0."""
        alert = TelegramAlert(bot_token="fake-token", chat_id="fake-chat")
        results = [_make_result("DIXON", "A+", 90), _make_result("RELIANCE", "A", 72)]

        result = alert.send(results, _RUN_DATE, _DISABLED_CONFIG)

        self.assertEqual(result.sent, 0)
        self.assertEqual(result.failed, 0)
        # Both input results should be counted as skipped
        self.assertEqual(result.skipped, len(results))
        self.assertIsNone(result.error)


class TestTelegramAlertNoQualifyingResults(unittest.TestCase):

    @patch("alerts.telegram_alert.requests.post")
    def test_no_qualifying_results_sends_no_setups_message(self, mock_post):
        """
        When all results are below min_quality, the message should still be
        sent (with the 'No A+/A setups today.' body) and sent=1.
        """
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        alert = TelegramAlert(bot_token="tok", chat_id="cid")
        # Only B and C quality — below "A" threshold
        results = [
            _make_result("ZOMATO", "B", 60),
            _make_result("PAYTM",  "C", 42),
        ]

        result = alert.send(results, _RUN_DATE, _BASE_CONFIG)

        self.assertEqual(result.sent, 1)
        self.assertEqual(result.skipped, 2)

        # Verify the message body contains the empty-setups phrase
        call_payload = mock_post.call_args[1]["json"]
        self.assertIn("No A", call_payload["text"])


class TestTelegramAlertAPlusResults(unittest.TestCase):

    @patch("alerts.telegram_alert.requests.post")
    def test_a_plus_results_are_sent(self, mock_post):
        """A+ and A results appear in the message; B results are excluded."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        alert = TelegramAlert(bot_token="tok", chat_id="cid")
        results = [
            _make_result("DIXON",    "A+", 92),
            _make_result("RELIANCE", "A",  74),
            _make_result("ZOMATO",   "B",  58),   # should be excluded
        ]

        result = alert.send(results, _RUN_DATE, _BASE_CONFIG)

        self.assertEqual(result.sent, 1)
        self.assertEqual(result.skipped, 1)   # ZOMATO skipped

        text = mock_post.call_args[1]["json"]["text"]
        self.assertIn("DIXON",    text)
        self.assertIn("RELIANCE", text)
        self.assertNotIn("ZOMATO", text)

        # A+ should appear before A in the sorted message
        self.assertLess(text.index("DIXON"), text.index("RELIANCE"))


class TestTelegramAlertWatchlist(unittest.TestCase):

    @patch("alerts.telegram_alert.requests.post")
    def test_watchlist_symbol_gets_star_prefix(self, mock_post):
        """
        A watchlist symbol with quality B and score >= min_score_alert must
        appear in the message with a '★' prefix.
        """
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        config = {
            "alerts": {"telegram": {"enabled": True, "min_quality": "A"}},
            "watchlist": {
                "min_score_alert": 55,
                "symbols": ["IRCTC"],
            },
        }

        alert = TelegramAlert(bot_token="tok", chat_id="cid")
        results = [
            _make_result("IRCTC", "B", 60),   # watchlist, qualifies via watchlist path
            _make_result("DIXON", "A", 75),    # normal A — no star
        ]

        result = alert.send(results, _RUN_DATE, config)

        self.assertEqual(result.sent, 1)
        text = mock_post.call_args[1]["json"]["text"]

        # IRCTC should appear (via watchlist path) with a star somewhere nearby
        self.assertIn("IRCTC", text)
        self.assertIn("★", text)

        # DIXON is an ordinary A — no star
        dixon_pos = text.index("DIXON")
        # Grab the 3 chars before DIXON's name to ensure no star immediately precedes it
        preceding = text[max(0, dixon_pos - 5): dixon_pos]
        self.assertNotIn("★", preceding)

    @patch("alerts.telegram_alert.requests.post")
    def test_watchlist_symbol_below_min_score_excluded(self, mock_post):
        """
        A watchlist symbol with quality B but score < min_score_alert must
        be skipped even though it is on the watchlist.
        """
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        config = {
            "alerts": {"telegram": {"enabled": True, "min_quality": "A"}},
            "watchlist": {
                "min_score_alert": 55,
                "symbols": ["IRCTC"],
            },
        }

        alert = TelegramAlert(bot_token="tok", chat_id="cid")
        results = [_make_result("IRCTC", "B", 40)]   # score 40 < 55

        result = alert.send(results, _RUN_DATE, config)

        text = mock_post.call_args[1]["json"]["text"]
        self.assertNotIn("IRCTC", text)
        self.assertEqual(result.skipped, 1)


class TestTelegramAlertRequestException(unittest.TestCase):

    @patch("alerts.telegram_alert.requests.post")
    def test_request_exception_raises_telegram_alert_error(self, mock_post):
        """
        A requests.RequestException must be caught and re-raised as
        TelegramAlertError (never propagating as the raw requests error).
        """
        mock_post.side_effect = requests.RequestException("connection refused")

        alert = TelegramAlert(bot_token="tok", chat_id="cid")
        results = [_make_result("DIXON", "A+", 90)]

        with self.assertRaises(TelegramAlertError):
            alert.send(results, _RUN_DATE, _BASE_CONFIG)

    @patch("alerts.telegram_alert.requests.post")
    def test_http_error_raises_telegram_alert_error(self, mock_post):
        """A non-2xx HTTP response (raise_for_status) also raises TelegramAlertError."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
        mock_post.return_value = mock_resp

        alert = TelegramAlert(bot_token="bad-token", chat_id="cid")
        results = [_make_result("DIXON", "A", 75)]

        with self.assertRaises(TelegramAlertError):
            alert.send(results, _RUN_DATE, _BASE_CONFIG)


if __name__ == "__main__":
    unittest.main()
