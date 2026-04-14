"""
tests/unit/test_runner_preflight.py
────────────────────────────────────
Unit tests for the _preflight_warnings() helper in pipeline/runner.py.

Strategy
────────
We do NOT spin up a full RunContext or touch the database.
_preflight_warnings only receives a plain dict and a logger, so we
inject a MagicMock logger and inspect the call count / arguments.

Coverage
────────
1. all_disabled  — every subsystem off  → ≥ 6 individual warnings
                                         + 1 combined "no alert channels" warning
2. all_enabled   — every subsystem on   → no warnings at all
3. partial       — only telegram on     → email + news warnings present;
                   no "no alert channels" warning (telegram covers it)
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call

from pipeline.runner import _preflight_warnings


# ─────────────────────────────────────────────────────────────────────────────
# Config fixtures
# ─────────────────────────────────────────────────────────────────────────────

_ALL_DISABLED: dict = {
    "news":          {"enabled": False},
    "paper_trading": {"enabled": False},
    "alerts": {
        "telegram": {"enabled": False},
        "email":    {"enabled": False},
    },
    "llm":          {"enabled": False},
    "fundamentals": {"enabled": False},
}

_ALL_ENABLED: dict = {
    "news":          {"enabled": True},
    "paper_trading": {"enabled": True},
    "alerts": {
        "telegram": {"enabled": True},
        "email":    {"enabled": True},
    },
    "llm":          {"enabled": True},
    "fundamentals": {"enabled": True},
}

# Only telegram is on; news + email remain off.
_TELEGRAM_ONLY: dict = {
    "news":          {"enabled": False},
    "paper_trading": {"enabled": True},
    "alerts": {
        "telegram": {"enabled": True},
        "email":    {"enabled": False},
    },
    "llm":          {"enabled": True},
    "fundamentals": {"enabled": True},
}


# ─────────────────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────────────────

class TestPreflightWarnings(unittest.TestCase):
    """Tests for _preflight_warnings(config, log)."""

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _make_log() -> MagicMock:
        """Return a fresh mock logger."""
        mock = MagicMock()
        mock.warning = MagicMock()
        return mock

    @staticmethod
    def _warning_messages(mock_log: MagicMock) -> list[str]:
        """Extract the positional message string from every warning() call."""
        return [c.args[0] for c in mock_log.warning.call_args_list]

    @staticmethod
    def _warning_subsystems(mock_log: MagicMock) -> list[str]:
        """Extract the `subsystem=` kwarg from every warning() call."""
        return [c.kwargs.get("subsystem", "") for c in mock_log.warning.call_args_list]


    # ── TC1: all subsystems disabled ─────────────────────────────────────────

    def test_all_disabled_fires_at_least_six_warnings(self) -> None:
        """With every subsystem off, _preflight_warnings must emit ≥ 6 warnings."""
        mock_log = self._make_log()
        _preflight_warnings(_ALL_DISABLED, mock_log)

        call_count = mock_log.warning.call_count
        self.assertGreaterEqual(
            call_count,
            6,
            msg=f"Expected ≥ 6 warning calls, got {call_count}",
        )

    def test_all_disabled_includes_six_specific_subsystems(self) -> None:
        """Each of the 6 named subsystem keys must appear in warnings."""
        mock_log = self._make_log()
        _preflight_warnings(_ALL_DISABLED, mock_log)

        subsystems = self._warning_subsystems(mock_log)
        expected_keys = [
            "news.enabled",
            "paper_trading.enabled",
            "alerts.telegram.enabled",
            "alerts.email.enabled",
            "llm.enabled",
            "fundamentals.enabled",
        ]
        for key in expected_keys:
            self.assertIn(
                key,
                subsystems,
                msg=f"Expected subsystem={key!r} warning not found",
            )

    def test_all_disabled_fires_combined_no_alert_channel_warning(self) -> None:
        """When news + telegram + email are all off, the combined warning fires."""
        mock_log = self._make_log()
        _preflight_warnings(_ALL_DISABLED, mock_log)

        subsystems = self._warning_subsystems(mock_log)
        self.assertIn(
            "alerts.all",
            subsystems,
            msg="Expected combined 'No alert channels' warning (subsystem='alerts.all')",
        )

    def test_all_disabled_combined_warning_message_text(self) -> None:
        """The combined warning must mention 'No alert channels'."""
        mock_log = self._make_log()
        _preflight_warnings(_ALL_DISABLED, mock_log)

        messages = self._warning_messages(mock_log)
        combined = [m for m in messages if "No alert channels" in m]
        self.assertTrue(
            combined,
            msg="Combined 'No alert channels' warning message text not found",
        )


    # ── TC2: all subsystems enabled ──────────────────────────────────────────

    def test_all_enabled_fires_no_warnings(self) -> None:
        """With every subsystem on, _preflight_warnings must not emit any warning."""
        mock_log = self._make_log()
        _preflight_warnings(_ALL_ENABLED, mock_log)

        self.assertEqual(
            mock_log.warning.call_count,
            0,
            msg=(
                f"Expected 0 warning calls when all subsystems are enabled, "
                f"got {mock_log.warning.call_count}: "
                f"{self._warning_messages(mock_log)}"
            ),
        )

    # ── TC3: partial config — telegram only ──────────────────────────────────

    def test_partial_no_combined_warning_when_telegram_is_on(self) -> None:
        """
        When at least one alert channel (telegram) is enabled, the combined
        'No alert channels' warning must NOT fire.
        """
        mock_log = self._make_log()
        _preflight_warnings(_TELEGRAM_ONLY, mock_log)

        subsystems = self._warning_subsystems(mock_log)
        self.assertNotIn(
            "alerts.all",
            subsystems,
            msg="Combined 'No alert channels' warning fired even though telegram is on",
        )

    def test_partial_news_and_email_disabled_warns(self) -> None:
        """In _TELEGRAM_ONLY config, news and email warnings must still fire."""
        mock_log = self._make_log()
        _preflight_warnings(_TELEGRAM_ONLY, mock_log)

        subsystems = self._warning_subsystems(mock_log)
        self.assertIn("news.enabled", subsystems)
        self.assertIn("alerts.email.enabled", subsystems)

    # ── TC4: empty / minimal config ──────────────────────────────────────────

    def test_empty_config_does_not_raise(self) -> None:
        """An empty dict must not raise; all subsystems default to disabled."""
        mock_log = self._make_log()
        try:
            _preflight_warnings({}, mock_log)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"_preflight_warnings raised {exc!r} on empty config")

    def test_empty_config_fires_warnings_for_all_subsystems(self) -> None:
        """An empty config means all subsystems are implicitly disabled → ≥ 6 warnings."""
        mock_log = self._make_log()
        _preflight_warnings({}, mock_log)

        self.assertGreaterEqual(
            mock_log.warning.call_count,
            6,
            msg="Expected ≥ 6 warnings for empty config (all defaults to disabled)",
        )

    # ── TC5: structured kwargs are always passed ──────────────────────────────

    def test_warnings_carry_subsystem_kwarg(self) -> None:
        """Every warning call must include a `subsystem` keyword argument."""
        mock_log = self._make_log()
        _preflight_warnings(_ALL_DISABLED, mock_log)

        for i, c in enumerate(mock_log.warning.call_args_list):
            self.assertIn(
                "subsystem",
                c.kwargs,
                msg=f"warning call #{i} is missing the `subsystem` kwarg: {c}",
            )


if __name__ == "__main__":
    unittest.main()
