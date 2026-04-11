"""
tests/unit/test_email_alert.py
──────────────────────────────
Unit tests for alerts/email_alert.py.

All SMTP calls are mocked so no real network traffic is generated.
"""

from __future__ import annotations

import datetime
import smtplib
import unittest
from unittest.mock import MagicMock, patch

from alerts.email_alert import EmailAlert, _build_plain, _build_html
from alerts.base import AlertResult
from rules.scorer import SEPAResult
from utils.exceptions import EmailAlertError


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_RUN_DATE = datetime.date(2025, 1, 15)

_BASE_CONFIG = {
    "alerts": {
        "email": {
            "enabled": True,
            "to": ["trader@example.com"],
            "from_addr": "bot@example.com",
            "min_quality": "A",
            "html": True,
        }
    },
    "watchlist": {"min_score_alert": 55},
}

_DISABLED_CONFIG = {
    "alerts": {"email": {"enabled": False, "to": ["x@x.com"]}},
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

class TestEmailAlertDisabled(unittest.TestCase):

    def test_disabled_returns_zero_sent(self):
        """When enabled=False send() returns immediately with sent=0."""
        alert = EmailAlert(smtp_user="u", smtp_pass="p")
        results = [_make_result("DIXON", "A+", 90), _make_result("TCS", "A", 72)]
        result = alert.send(results, _RUN_DATE, _DISABLED_CONFIG)
        self.assertEqual(result.sent,    0)
        self.assertEqual(result.failed,  0)
        self.assertEqual(result.skipped, len(results))
        self.assertIsNone(result.error)

    def test_no_recipients_returns_zero_sent(self):
        """When 'to' list is empty send() skips without raising."""
        cfg = {
            "alerts": {"email": {"enabled": True, "to": []}},
            "watchlist": {"min_score_alert": 55},
        }
        alert = EmailAlert(smtp_user="u", smtp_pass="p")
        result = alert.send([_make_result()], _RUN_DATE, cfg)
        self.assertEqual(result.sent, 0)
        self.assertIsNotNone(result.error)


# ─────────────────────────────────────────────────────────────────────────────
# Test: filtering / quality gate
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailAlertFiltering(unittest.TestCase):

    @patch("alerts.email_alert.EmailAlert._smtp_send")
    def test_only_a_and_a_plus_sent_by_default(self, mock_send):
        """B and C quality setups are excluded when min_quality='A'."""
        alert = EmailAlert(smtp_user="u", smtp_pass="p")
        results = [
            _make_result("DIXON",    "A+", 91),
            _make_result("RELIANCE", "A",  74),
            _make_result("ZOMATO",   "B",  60),
            _make_result("PAYTM",    "C",  42),
        ]
        ar = alert.send(results, _RUN_DATE, _BASE_CONFIG)
        self.assertEqual(ar.sent,    1)
        self.assertEqual(ar.skipped, 2)
        mock_send.assert_called_once()

    @patch("alerts.email_alert.EmailAlert._smtp_send")
    def test_watchlist_b_above_min_score_included(self, mock_send):
        """Watchlist symbols with B quality and score >= min_score_alert pass."""
        cfg = {
            "alerts": {"email": {"enabled": True, "to": ["x@x.com"],
                                  "min_quality": "A", "html": False}},
            "watchlist": {"min_score_alert": 55, "symbols": ["IRCTC"]},
        }
        alert = EmailAlert(smtp_user="u", smtp_pass="p")
        results = [_make_result("IRCTC", "B", 60)]
        ar = alert.send(results, _RUN_DATE, cfg)
        self.assertEqual(ar.sent,    1)
        self.assertEqual(ar.skipped, 0)

    @patch("alerts.email_alert.EmailAlert._smtp_send")
    def test_watchlist_b_below_min_score_excluded(self, mock_send):
        """Watchlist B symbol with score below threshold is excluded."""
        cfg = {
            "alerts": {"email": {"enabled": True, "to": ["x@x.com"],
                                  "min_quality": "A", "html": False}},
            "watchlist": {"min_score_alert": 55, "symbols": ["IRCTC"]},
        }
        alert = EmailAlert(smtp_user="u", smtp_pass="p")
        results = [_make_result("IRCTC", "B", 40)]
        ar = alert.send(results, _RUN_DATE, cfg)
        self.assertEqual(ar.skipped, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Test: sort order
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailAlertSortOrder(unittest.TestCase):

    @patch("alerts.email_alert.EmailAlert._smtp_send")
    def test_a_plus_before_a_in_plain_body(self, mock_send):
        """A+ setups must appear before A setups in the plain-text body."""
        alert = EmailAlert(smtp_user="u", smtp_pass="p")
        results = [
            _make_result("RELIANCE", "A",  74),
            _make_result("DIXON",    "A+", 91),
        ]
        alert.send(results, _RUN_DATE, _BASE_CONFIG)
        msg = mock_send.call_args[0][0]
        plain_part = msg.get_payload(0).get_payload(decode=True).decode()
        self.assertLess(plain_part.index("DIXON"), plain_part.index("RELIANCE"))


# ─────────────────────────────────────────────────────────────────────────────
# Test: SMTP error handling
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailAlertSmtpErrors(unittest.TestCase):

    @patch("alerts.email_alert.smtplib.SMTP")
    def test_smtp_exception_raises_email_alert_error(self, mock_smtp_cls):
        """smtplib.SMTPException must be re-raised as EmailAlertError."""
        mock_smtp = MagicMock()
        mock_smtp.ehlo.return_value = None
        mock_smtp.starttls.return_value = None
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad auth")
        mock_smtp_cls.return_value = mock_smtp

        alert = EmailAlert(smtp_host="smtp.test", smtp_port=587,
                           smtp_user="u", smtp_pass="wrong")
        results = [_make_result("DIXON", "A+", 90)]

        with self.assertRaises(EmailAlertError):
            alert.send(results, _RUN_DATE, _BASE_CONFIG)

    @patch("alerts.email_alert.smtplib.SMTP")
    def test_socket_error_raises_email_alert_error(self, mock_smtp_cls):
        """OSError (e.g. host unreachable) must also raise EmailAlertError."""
        mock_smtp_cls.side_effect = OSError("Network unreachable")

        alert = EmailAlert(smtp_host="bad.host", smtp_port=587,
                           smtp_user="u", smtp_pass="p")
        results = [_make_result("RELIANCE", "A", 74)]

        with self.assertRaises(EmailAlertError):
            alert.send(results, _RUN_DATE, _BASE_CONFIG)


# ─────────────────────────────────────────────────────────────────────────────
# Test: message content helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailAlertMessageContent(unittest.TestCase):

    def test_plain_body_contains_symbol(self):
        kept = [(_make_result("DIXON", "A+", 91), False)]
        body = _build_plain(kept, _RUN_DATE)
        self.assertIn("DIXON", body)
        self.assertIn("A+",    body)
        self.assertIn("2025-01-15", body)

    def test_plain_body_empty_setups(self):
        body = _build_plain([], _RUN_DATE)
        self.assertIn("No A+/A setups", body)

    def test_html_body_contains_symbol(self):
        kept = [(_make_result("RELIANCE", "A", 74), True)]
        html = _build_html(kept, _RUN_DATE)
        self.assertIn("RELIANCE", html)
        self.assertIn("★",        html)

    def test_html_body_empty_setups(self):
        html = _build_html([], _RUN_DATE)
        self.assertIn("No A+/A setups", html)

    def test_rr_ratio_appears_in_plain(self):
        result = _make_result("DIXON", "A+", 91, rr_ratio=2.5, target_price=5000.0)
        kept = [(result, False)]
        body = _build_plain(kept, _RUN_DATE)
        self.assertIn("2.50", body)

    def test_none_rr_ratio_does_not_crash(self):
        result = _make_result("DIXON", "A+", 91, rr_ratio=None, target_price=None)
        kept = [(result, False)]
        body = _build_plain(kept, _RUN_DATE)
        self.assertIn("DIXON", body)

    @patch("alerts.email_alert.EmailAlert._smtp_send")
    def test_ssl_port_path_calls_smtp_send(self, mock_send):
        """Port 465 path must still call _smtp_send successfully."""
        alert = EmailAlert(smtp_host="smtp.test", smtp_port=465,
                           smtp_user="u", smtp_pass="p")
        results = [_make_result("DIXON", "A+", 90)]
        ar = alert.send(results, _RUN_DATE, _BASE_CONFIG)
        self.assertEqual(ar.sent, 1)
        mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
