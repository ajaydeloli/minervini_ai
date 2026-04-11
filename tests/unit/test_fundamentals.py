"""
tests/unit/test_fundamentals.py
────────────────────────────────
Full unit-test suite for:
  • ingestion/fundamentals.py   — is_cache_valid(), fetch_fundamentals()
  • rules/fundamental_template.py — check_fundamental_template()

Test groups
───────────
  GROUP 1  is_cache_valid()                         3 tests
  GROUP 2  fetch_fundamentals() with mocking         6 tests
  GROUP 3  check_fundamental_template() — None input 2 tests
  GROUP 4  check_fundamental_template() — all pass   2 tests
  GROUP 5  individual F1–F7 condition failures       7 tests
  GROUP 6  hard_gate behaviour                       2 tests
  GROUP 7  None field graceful handling              2 tests
  GROUP 8  fundamental_score calculation             2 tests
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingestion.fundamentals import fetch_fundamentals, is_cache_valid
from rules.fundamental_template import FundamentalResult, check_fundamental_template

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))


def make_fundamentals(**overrides) -> dict:
    """
    Return a valid fundamentals dict with all 7 condition fields set to
    passing values.  Keyword overrides replace individual keys.
    """
    base = {
        "symbol":           "TESTCO",
        "fetched_at":       datetime.now(_IST).isoformat(),
        # F1 — EPS positive
        "eps":              10.0,
        # F2 — EPS accelerating
        "eps_accelerating": True,
        # F3 — Sales growth >= 10 %
        "sales_growth_yoy": 20.0,
        # F4 — ROE >= 15 %
        "roe":              25.0,
        # F5 — D/E <= 1.0
        "debt_to_equity":   0.4,
        # F6 — Promoter holding >= 35 %
        "promoter_holding": 60.0,
        # F7 — Profit growth > 0
        "profit_growth":    15.0,
        # non-condition fields (inert for template)
        "pe_ratio":         22.0,
        "pb_ratio":          3.0,
        "roce":             18.0,
        "fii_holding_pct":   8.0,
        "fii_trend":        "rising",
        "eps_values":       [8.0, 9.0, 10.0, 11.0],
        "eps_growth_rates": [12.5, 11.1, 10.0],
        "latest_revenue":   5000.0,
        "latest_profit":     800.0,
    }
    base.update(overrides)
    return base


def make_config(**overrides) -> dict:
    """
    Return a minimal config dict matching the structure consumed by both
    fetch_fundamentals() and check_fundamental_template().
    Defaults: enabled=True, hard_gate=False, all thresholds at spec defaults.
    """
    fund = {
        "enabled":    True,
        "hard_gate":  False,
        "cache_days": 7,
        "conditions": {
            "min_sales_growth_yoy": 10.0,
            "min_roe":              15.0,
            "max_de":                1.0,
            "min_promoter_holding": 35.0,
        },
    }
    fund.update(overrides)
    return {
        "fundamentals": fund,
        "data": {
            "fundamentals_dir": "/tmp/minervini_test_cache",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 1 — is_cache_valid()
# ─────────────────────────────────────────────────────────────────────────────

class TestIsCacheValid:
    """is_cache_valid(cache_path, cache_days) -> bool"""

    def _write_cache(self, path: Path, age_hours: float = 0) -> None:
        """Write a minimal cache file with fetched_at set *age_hours* ago."""
        fetched_at = datetime.now(_IST) - timedelta(hours=age_hours)
        data = {
            "symbol":     "TESTCO",
            "fetched_at": fetched_at.isoformat(),
            "eps":        10.0,
        }
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_returns_true_when_cache_fresh(self, tmp_path):
        """Cache file exists and fetched_at is within TTL → True."""
        cache_file = tmp_path / "TESTCO.json"
        self._write_cache(cache_file, age_hours=1)   # 1 hour old; TTL = 7 days
        assert is_cache_valid(cache_file, cache_days=7) is True

    def test_returns_false_when_cache_expired(self, tmp_path):
        """Cache file exists but fetched_at is older than cache_days → False."""
        cache_file = tmp_path / "TESTCO.json"
        self._write_cache(cache_file, age_hours=8 * 24)  # 8 days old; TTL = 7 days
        assert is_cache_valid(cache_file, cache_days=7) is False

    def test_returns_false_when_file_missing(self, tmp_path):
        """Cache file does not exist → False (no exception raised)."""
        cache_file = tmp_path / "NONEXISTENT.json"
        assert is_cache_valid(cache_file, cache_days=7) is False

    def test_returns_false_when_fetched_at_missing(self, tmp_path):
        """Cache JSON exists but has no 'fetched_at' key → False."""
        cache_file = tmp_path / "TESTCO.json"
        cache_file.write_text(json.dumps({"eps": 10.0}), encoding="utf-8")
        assert is_cache_valid(cache_file, cache_days=7) is False

    def test_returns_false_when_json_corrupt(self, tmp_path):
        """Cache file is corrupt (not valid JSON) → False, no exception."""
        cache_file = tmp_path / "TESTCO.json"
        cache_file.write_text("not-valid-json{{{{", encoding="utf-8")
        assert is_cache_valid(cache_file, cache_days=7) is False


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 2 — fetch_fundamentals() with mocking
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchFundamentals:
    """fetch_fundamentals(symbol, config, force_refresh=False) -> dict | None"""

    SYMBOL = "TESTCO"

    def _cache_path(self, config: dict) -> Path:
        return Path(config["data"]["fundamentals_dir"]) / f"{self.SYMBOL}.json"

    def _write_valid_cache(self, config: dict, age_hours: float = 1) -> dict:
        """Write a fresh cache file and return the fundamentals dict written."""
        fund_dir = Path(config["data"]["fundamentals_dir"])
        fund_dir.mkdir(parents=True, exist_ok=True)
        data = make_fundamentals(
            fetched_at=(datetime.now(_IST) - timedelta(hours=age_hours)).isoformat()
        )
        cache_path = self._cache_path(config)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    # ── test 1: disabled returns None ────────────────────────────────────────

    def test_returns_none_when_disabled(self, tmp_path):
        """fundamentals.enabled=False → None immediately, no HTTP call."""
        config = make_config(enabled=False)
        config["data"]["fundamentals_dir"] = str(tmp_path)

        with patch("ingestion.fundamentals._fetch_from_screener") as mock_fetch:
            result = fetch_fundamentals(self.SYMBOL, config)

        assert result is None
        mock_fetch.assert_not_called()

    # ── test 2: cache hit returns cached dict ────────────────────────────────

    def test_returns_cached_dict_without_http_call(self, tmp_path):
        """Valid cache exists → cached dict returned, no HTTP call made."""
        config = make_config()
        config["data"]["fundamentals_dir"] = str(tmp_path)
        cached = self._write_valid_cache(config)

        with patch("ingestion.fundamentals._fetch_from_screener") as mock_fetch:
            result = fetch_fundamentals(self.SYMBOL, config)

        assert result is not None
        assert result["eps"] == cached["eps"]
        mock_fetch.assert_not_called()

    # ── test 3: cache miss → HTTP call + writes cache ────────────────────────

    def test_makes_http_call_and_writes_cache_when_cache_missing(self, tmp_path):
        """No cache file → _fetch_from_screener called; result written to disk."""
        config = make_config()
        config["data"]["fundamentals_dir"] = str(tmp_path)
        fetched_data = make_fundamentals()

        with patch("ingestion.fundamentals._fetch_from_screener", return_value=fetched_data):
            result = fetch_fundamentals(self.SYMBOL, config)

        assert result is not None
        assert result["eps"] == fetched_data["eps"]
        cache_path = self._cache_path(config)
        assert cache_path.exists(), "Cache file should have been written"

    # ── test 4: network failure → returns None ───────────────────────────────

    def test_returns_none_on_network_failure(self, tmp_path):
        """_fetch_from_screener returns None (network error) → None returned."""
        config = make_config()
        config["data"]["fundamentals_dir"] = str(tmp_path)

        with patch("ingestion.fundamentals._fetch_from_screener", return_value=None):
            result = fetch_fundamentals(self.SYMBOL, config)

        assert result is None

    # ── test 5: force_refresh bypasses valid cache ───────────────────────────

    def test_force_refresh_bypasses_valid_cache(self, tmp_path):
        """force_refresh=True → HTTP call even when cache is fresh."""
        config = make_config()
        config["data"]["fundamentals_dir"] = str(tmp_path)
        self._write_valid_cache(config)                     # fresh cache
        fresh_data = make_fundamentals(eps=999.0)           # distinct value

        with patch("ingestion.fundamentals._fetch_from_screener", return_value=fresh_data):
            result = fetch_fundamentals(self.SYMBOL, config, force_refresh=True)

        assert result is not None
        assert result["eps"] == 999.0

    # ── test 6: stale cache + fetch failure → None ───────────────────────────

    def test_returns_none_when_cache_stale_and_fetch_fails(self, tmp_path):
        """Expired cache + _fetch_from_screener=None → None returned."""
        config = make_config()
        config["data"]["fundamentals_dir"] = str(tmp_path)
        self._write_valid_cache(config, age_hours=8 * 24)   # 8 days old

        with patch("ingestion.fundamentals._fetch_from_screener", return_value=None):
            result = fetch_fundamentals(self.SYMBOL, config)

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 3 — check_fundamental_template() — None input
# ─────────────────────────────────────────────────────────────────────────────

class TestFundamentalTemplateNoneInput:
    """fundamentals=None triggers graceful-degradation path."""

    def test_none_input_returns_passes_false(self):
        result = check_fundamental_template(None, make_config())
        assert isinstance(result, FundamentalResult)
        assert result.passes is False
        assert result.conditions_met == 0
        assert result.fundamental_score == 0.0

    def test_none_input_all_conditions_false(self):
        result = check_fundamental_template(None, make_config())
        for key in ("F1", "F2", "F3", "F4", "F5", "F6", "F7"):
            assert result.conditions[key] is False, f"Expected {key}=False for None input"
        assert result.hard_fails == ["no_data"]


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 4 — check_fundamental_template() — all pass
# ─────────────────────────────────────────────────────────────────────────────

class TestFundamentalTemplateAllPass:
    """Valid dict with all 7 fields satisfying thresholds."""

    def test_all_seven_pass_soft_gate(self):
        result = check_fundamental_template(make_fundamentals(), make_config())
        assert result.passes is True
        assert result.conditions_met == 7
        assert result.fundamental_score == pytest.approx(100.0)

    def test_all_seven_pass_hard_gate(self):
        config = make_config(hard_gate=True)
        result = check_fundamental_template(make_fundamentals(), config)
        assert result.passes is True
        assert result.conditions_met == 7


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 5 — individual condition failures (one test per condition)
# ─────────────────────────────────────────────────────────────────────────────

class TestIndividualConditionFailures:
    """Verify each condition independently fails when its field is bad."""

    def test_f1_fails_when_eps_zero(self):
        """F1: eps=0 → F1=False (must be strictly > 0)."""
        result = check_fundamental_template(
            make_fundamentals(eps=0.0), make_config()
        )
        assert result.conditions["F1"] is False

    def test_f1_fails_when_eps_negative(self):
        """F1: eps=-5 → F1=False."""
        result = check_fundamental_template(
            make_fundamentals(eps=-5.0), make_config()
        )
        assert result.conditions["F1"] is False

    def test_f2_fails_when_eps_not_accelerating(self):
        """F2: eps_accelerating=False → F2=False."""
        result = check_fundamental_template(
            make_fundamentals(eps_accelerating=False), make_config()
        )
        assert result.conditions["F2"] is False

    def test_f3_fails_when_sales_growth_below_threshold(self):
        """F3: sales_growth_yoy=8.0 (< 10.0) → F3=False."""
        result = check_fundamental_template(
            make_fundamentals(sales_growth_yoy=8.0), make_config()
        )
        assert result.conditions["F3"] is False

    def test_f4_fails_when_roe_below_threshold(self):
        """F4: roe=10.0 (< 15.0) → F4=False."""
        result = check_fundamental_template(
            make_fundamentals(roe=10.0), make_config()
        )
        assert result.conditions["F4"] is False

    def test_f5_fails_when_de_above_threshold(self):
        """F5: debt_to_equity=1.5 (> 1.0) → F5=False."""
        result = check_fundamental_template(
            make_fundamentals(debt_to_equity=1.5), make_config()
        )
        assert result.conditions["F5"] is False

    def test_f6_fails_when_promoter_holding_below_threshold(self):
        """F6: promoter_holding=30.0 (< 35.0) → F6=False."""
        result = check_fundamental_template(
            make_fundamentals(promoter_holding=30.0), make_config()
        )
        assert result.conditions["F6"] is False

    def test_f7_fails_when_profit_growth_negative(self):
        """F7: profit_growth=-5.0 (< 0) → F7=False."""
        result = check_fundamental_template(
            make_fundamentals(profit_growth=-5.0), make_config()
        )
        assert result.conditions["F7"] is False


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 6 — hard_gate behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestHardGateBehaviour:
    """
    hard_gate=False (soft): passes when conditions_met >= 4.
    hard_gate=True  (hard): passes only when conditions_met == 7.
    """

    def test_soft_gate_passes_with_four_conditions(self):
        """hard_gate=False, 4 conditions met → passes=True."""
        # Fail F1, F2, F5 — leave F3, F4, F6, F7 passing (4 of 7)
        fund = make_fundamentals(
            eps=-1.0,               # F1 fails
            eps_accelerating=False, # F2 fails
            debt_to_equity=2.0,     # F5 fails
        )
        result = check_fundamental_template(fund, make_config(hard_gate=False))
        assert result.conditions_met == 4
        assert result.passes is True

    def test_hard_gate_fails_with_six_conditions(self):
        """hard_gate=True, 6 of 7 pass → passes=False (all 7 required)."""
        fund = make_fundamentals(eps_accelerating=False)  # F2 fails only
        result = check_fundamental_template(fund, make_config(hard_gate=True))
        assert result.conditions_met == 6
        assert result.passes is False


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 7 — None field graceful handling
# ─────────────────────────────────────────────────────────────────────────────

class TestNoneFieldGracefulHandling:
    """None values in the dict must not raise; they evaluate to False."""

    def test_single_none_field_causes_false_condition_no_exception(self):
        """One field None → that condition False, no exception raised, others unaffected."""
        fund = make_fundamentals(roe=None)   # F4 field is None
        result = check_fundamental_template(fund, make_config())
        assert result.conditions["F4"] is False
        # All other 6 conditions should still pass
        assert result.conditions_met == 6

    def test_all_fields_none_returns_zero_conditions_no_exception(self):
        """All 7 condition fields None → conditions_met=0, passes=False, no exception."""
        fund = make_fundamentals(
            eps=None,
            eps_accelerating=None,
            sales_growth_yoy=None,
            roe=None,
            debt_to_equity=None,
            promoter_holding=None,
            profit_growth=None,
        )
        result = check_fundamental_template(fund, make_config())
        assert result.conditions_met == 0
        assert result.passes is False
        for key in ("F1", "F2", "F3", "F4", "F5", "F6", "F7"):
            assert result.conditions[key] is False


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 8 — fundamental_score calculation
# ─────────────────────────────────────────────────────────────────────────────

class TestFundamentalScoreCalculation:
    """fundamental_score = (conditions_met / 7) * 100.0"""

    def test_score_is_approx_correct_for_partial_pass(self):
        """3 of 7 conditions pass → score ≈ 42.86."""
        # Pass only F3, F4, F6; fail F1, F2, F5, F7
        fund = make_fundamentals(
            eps=-1.0,               # F1 fails
            eps_accelerating=False, # F2 fails
            debt_to_equity=2.0,     # F5 fails
            profit_growth=-1.0,     # F7 fails
        )
        result = check_fundamental_template(fund, make_config())
        assert result.conditions_met == 3
        assert result.fundamental_score == pytest.approx((3 / 7) * 100.0, rel=1e-4)

    def test_score_is_100_when_all_pass(self):
        """All 7 conditions pass → fundamental_score == 100.0 exactly."""
        result = check_fundamental_template(make_fundamentals(), make_config())
        assert result.fundamental_score == pytest.approx(100.0)

    def test_score_is_zero_for_none_input(self):
        """fundamentals=None (no data) → fundamental_score == 0.0."""
        result = check_fundamental_template(None, make_config())
        assert result.fundamental_score == 0.0

    def test_score_reflects_exact_conditions_met(self):
        """Score formula: (conditions_met / 7) * 100."""
        for fail_count in range(8):
            # Fail exactly fail_count conditions by setting fields to bad values
            overrides: dict = {}
            fail_map = [
                ("eps", -1.0),
                ("eps_accelerating", False),
                ("sales_growth_yoy", 1.0),
                ("roe", 1.0),
                ("debt_to_equity", 5.0),
                ("promoter_holding", 5.0),
                ("profit_growth", -1.0),
            ]
            for i in range(fail_count):
                k, v = fail_map[i]
                overrides[k] = v

            fund = make_fundamentals(**overrides)
            result = check_fundamental_template(fund, make_config())
            expected_met = 7 - fail_count
            expected_score = (expected_met / 7) * 100.0
            assert result.conditions_met == expected_met, (
                f"fail_count={fail_count}: expected conditions_met={expected_met}, "
                f"got {result.conditions_met}"
            )
            assert result.fundamental_score == pytest.approx(expected_score, rel=1e-4)
