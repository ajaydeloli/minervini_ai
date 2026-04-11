"""
tests/unit/test_webhook_alert.py
─────────────────────────────────
Unit tests for alerts/webhook_alert.py.

All HTTP calls are mocked — no real network traffic.
"""

from __future__ import annotations

import datetime
import json
import unittest
from unittest.mock import MagicMock, call, patch

import requests

from alerts.webhook_alert import WebhookAlert, _slack_payload, _plain_payload
from alerts.base import AlertResult
from rules.scorer import SEPAResult
from utils.exceptions import WebhookAlertError


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_RUN_DATE = datetime.date(2025, 1, 15)

_BASE_CONFIG = {
    "alerts": {
        "webhook": {
            "enabled": True,
            "url": ["https://hooks.slack.com/test/abc"],
            "format": "slack",
            "min_quality": "A",
        }
    },
    "watchlist": {"min_score_alert": 55},
}

_DISABLED_CONFIG = {
    "alerts": {"webhook": {"enabled": False, "url": ["https://x.example.com"]}},
    "watchlist": {"min_score_alert": 55},
}


def _make_result(
    symbol: str = "DIXON",
    setup_quality: str = "A",
    score: int = 75,
    stage: int = 2,
    stage_label: str = "Stage 2 — Advancing",
    rs_rating: int = 85,
    entry_price: float = 4500.0,
    stop_loss: float = 4230.0,
    risk_pct: float = 6.0,
    rr_ratio: float | None = 2.5,
    target_price: float | None = 4900.0,
    vcp_qualified: bool = True,
    breakout_triggered: bool = True,
) -> SEPAResult:
    return SEPAResult(
        symbol=symbol,
        date=_RUN_DATE,
        stage=stage,
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
        stop_type="vcp",
        risk_pct=risk_pct,
        rr_ratio=rr_ratio,
        target_price=target_price,
        rs_rating=rs_rating,
        setup_quality=setup_quality,
        score=score,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test: disabled path
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookAlertDisabled(unittest.TestCase):

    def test_disabled_returns_zero_sent(self):
        """When enabled=False send() returns immediately with sent=0."""
        alert = WebhookAlert()
        results = [_make_result("DIXON", "A+", 90), _make_result("TCS", "A", 72)]
        ar = alert.send(results, _RUN_DATE, _DISABLED_CONFIG)
        self.assertEqual(ar.sent,    0)
        self.assertEqual(ar.failed,  0)
        self.assertEqual(ar.skipped, len(results))

    def test_no_urls_returns_zero_sent(self):
        """When no URLs configured, send() skips without raising."""
        cfg = {
            "alerts": {"webhook": {"enabled": True, "url": []}},
            "watchlist": {"min_score_alert": 55},
        }
        alert = WebhookAlert()
        ar = alert.send([_make_result()], _RUN_DATE, cfg)
        self.assertEqual(ar.sent, 0)
        self.assertIsNotNone(ar.error)


# ─────────────────────────────────────────────────────────────────────────────
# Test: successful dispatch
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookAlertSuccess(unittest.TestCase):

    @patch("alerts.webhook_alert.requests.post")
    def test_single_url_success(self, mock_post):
        """One URL that returns 200 → sent=1, failed=0."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        alert = WebhookAlert()
        results = [_make_result("DIXON", "A+", 91), _make_result("ZOMATO", "B", 60)]
        ar = alert.send(results, _RUN_DATE, _BASE_CONFIG)

        self.assertEqual(ar.sent,    1)
        self.assertEqual(ar.failed,  0)
        self.assertEqual(ar.skipped, 1)   # ZOMATO below A threshold
        mock_post.assert_called_once()

    @patch("alerts.webhook_alert.requests.post")
    def test_multiple_urls_all_succeed(self, mock_post):
        """All URLs succeed → sent == number of URLs."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        cfg = {
            "alerts": {
                "webhook": {
                    "enabled": True,
                    "url": ["https://hook1.example.com", "https://hook2.example.com"],
                    "format": "slack",
                    "min_quality": "A",
                }
            },
            "watchlist": {"min_score_alert": 55},
        }
        alert = WebhookAlert()
        results = [_make_result("DIXON", "A+", 91)]
        ar = alert.send(results, _RUN_DATE, cfg)

        self.assertEqual(ar.sent,   2)
        self.assertEqual(ar.failed, 0)
        self.assertEqual(mock_post.call_count, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Test: partial failure
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookAlertPartialFailure(unittest.TestCase):

    @patch("alerts.webhook_alert.requests.post")
    def test_one_url_fails_does_not_raise(self, mock_post):
        """When some (but not all) URLs fail, AlertResult.failed is set and no exception."""
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None

        fail_resp = MagicMock()
        fail_resp.raise_for_status.side_effect = requests.HTTPError("500")

        mock_post.side_effect = [ok_resp, fail_resp]

        cfg = {
            "alerts": {
                "webhook": {
                    "enabled": True,
                    "url": ["https://ok.example.com", "https://fail.example.com"],
                    "format": "slack",
                    "min_quality": "A",
                }
            },
            "watchlist": {"min_score_alert": 55},
        }
        alert = WebhookAlert()
        results = [_make_result("DIXON", "A+", 91)]
        ar = alert.send(results, _RUN_DATE, cfg)   # must NOT raise

        self.assertEqual(ar.sent,   1)
        self.assertEqual(ar.failed, 1)
        self.assertIsNotNone(ar.error)

    @patch("alerts.webhook_alert.requests.post")
    def test_all_urls_fail_raises_webhook_alert_error(self, mock_post):
        """When every URL fails, WebhookAlertError must be raised."""
        mock_post.side_effect = requests.RequestException("timeout")

        alert = WebhookAlert()
        results = [_make_result("DIXON", "A+", 91)]

        with self.assertRaises(WebhookAlertError):
            alert.send(results, _RUN_DATE, _BASE_CONFIG)


# ─────────────────────────────────────────────────────────────────────────────
# Test: filtering mirrors Telegram logic
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookAlertFiltering(unittest.TestCase):

    @patch("alerts.webhook_alert.requests.post")
    def test_b_quality_excluded_by_default(self, mock_post):
        mock_resp = MagicMock(); mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        alert = WebhookAlert()
        results = [_make_result("ZOMATO", "B", 60)]
        ar = alert.send(results, _RUN_DATE, _BASE_CONFIG)
        self.assertEqual(ar.skipped, 1)

    @patch("alerts.webhook_alert.requests.post")
    def test_watchlist_b_above_min_score_included(self, mock_post):
        mock_resp = MagicMock(); mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        cfg = {
            "alerts": {"webhook": {
                "enabled": True, "url": ["https://x.example.com"],
                "format": "slack", "min_quality": "A",
            }},
            "watchlist": {"min_score_alert": 55, "symbols": ["IRCTC"]},
        }
        alert = WebhookAlert()
        results = [_make_result("IRCTC", "B", 60)]
        ar = alert.send(results, _RUN_DATE, cfg)
        self.assertEqual(ar.skipped, 0)
        self.assertEqual(ar.sent,    1)


# ─────────────────────────────────────────────────────────────────────────────
# Test: payload builders
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookPayloadBuilders(unittest.TestCase):

    def _kept(self) -> list[tuple[SEPAResult, bool]]:
        return [
            (_make_result("DIXON",    "A+", 91), False),
            (_make_result("RELIANCE", "A",  74), True),
        ]

    def test_slack_payload_has_text_and_blocks(self):
        payload = _slack_payload(self._kept(), _RUN_DATE)
        self.assertIn("text",   payload)
        self.assertIn("blocks", payload)
        full_text = json.dumps(payload)
        self.assertIn("DIXON",    full_text)
        self.assertIn("RELIANCE", full_text)

    def test_slack_payload_empty_setups(self):
        payload = _slack_payload([], _RUN_DATE)
        full_text = json.dumps(payload)
        self.assertIn("No A+/A setups", full_text)

    def test_plain_payload_structure(self):
        payload = _plain_payload(self._kept(), _RUN_DATE)
        self.assertIn("run_date", payload)
        self.assertIn("summary",  payload)
        self.assertIn("setups",   payload)
        self.assertEqual(payload["summary"]["a_plus"], 1)
        self.assertEqual(payload["summary"]["a"],      1)
        self.assertEqual(len(payload["setups"]),       2)

    def test_plain_payload_setup_fields(self):
        payload = _plain_payload(self._kept(), _RUN_DATE)
        setup = payload["setups"][0]
        for key in ("symbol", "setup_quality", "score", "entry_price",
                    "stop_loss", "rr_ratio", "vcp_qualified"):
            self.assertIn(key, setup, f"Missing key: {key}")

    @patch("alerts.webhook_alert.requests.post")
    def test_plain_format_dispatched_correctly(self, mock_post):
        """format='plain' must produce a payload with 'setups' key (not Slack blocks)."""
        mock_resp = MagicMock(); mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        cfg = {
            "alerts": {"webhook": {
                "enabled": True, "url": ["https://x.example.com"],
                "format": "plain", "min_quality": "A",
            }},
            "watchlist": {"min_score_alert": 55},
        }
        alert = WebhookAlert()
        results = [_make_result("DIXON", "A+", 91)]
        alert.send(results, _RUN_DATE, cfg)

        sent_body = json.loads(mock_post.call_args[1]["data"])
        self.assertIn("setups",  sent_body)
        self.assertNotIn("blocks", sent_body)


# ─────────────────────────────────────────────────────────────────────────────
# Test: URL deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookUrlDedup(unittest.TestCase):

    @patch("alerts.webhook_alert.requests.post")
    def test_duplicate_urls_posted_once(self, mock_post):
        """If the same URL appears in constructor AND config it is POSTed only once."""
        mock_resp = MagicMock(); mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        url = "https://hooks.slack.com/dup"
        cfg = {
            "alerts": {"webhook": {
                "enabled": True, "url": [url],
                "format": "slack", "min_quality": "A",
            }},
            "watchlist": {"min_score_alert": 55},
        }
        alert = WebhookAlert(urls=[url])   # same URL injected via constructor
        results = [_make_result("DIXON", "A+", 91)]
        ar = alert.send(results, _RUN_DATE, cfg)

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(ar.sent, 1)


if __name__ == "__main__":
    unittest.main()
