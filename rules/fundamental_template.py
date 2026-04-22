"""
rules/fundamental_template.py
──────────────────────────────
Minervini fundamental conditions evaluation for the SEPA rule engine.

Overview
────────
Minervini's fundamental template is a set of 7 conditions that assess the
earnings quality and balance-sheet health of a stock.  This module evaluates
all 7 conditions against a fundamentals dict (sourced from
ingestion/fundamentals.py) and returns a fully populated FundamentalResult.

This layer runs AFTER the rule engine (Stage 2 + Trend Template) — only
on stocks that have already passed the technical gate.  If fundamentals
data is unavailable (scraper returned None), the function returns a
graceful-degradation result with passes=False and conditions_met=0; the
pipeline MUST NOT be crashed by a missing fundamentals fetch.

The 7 Conditions  (PROJECT_DESIGN.md Appendix D)
─────────────────────────────────────────────────
    F1: EPS positive           — fundamentals["eps"] is not None AND > 0
    F2: EPS accelerating       — fundamentals["eps_accelerating"] is True
    F3: Sales growth >= 10% YoY — fundamentals["sales_growth_yoy"] >= threshold
    F4: ROE >= 15%             — fundamentals["roe"] >= threshold
    F5: D/E ratio <= 1.0       — fundamentals["debt_to_equity"] <= threshold
    F6: Promoter holding >= 35% — fundamentals["promoter_holding"] >= threshold
    F7: Positive profit growth  — fundamentals["profit_growth"] is not None AND > 0

Public API
──────────
    check_fundamental_template(fundamentals, config) → FundamentalResult

Graceful-degradation contract  (PROJECT_DESIGN.md §9.1, §17.3, §19.1)
───────────────────────────────────────────────────────────────────────
    fundamentals=None         → FundamentalResult(passes=False, conditions_met=0,
                                  hard_fails=["no_data"], fundamental_score=0.0)
    Any field None in dict    → that condition evaluates to False; no exception raised.
    This module NEVER raises.  Missing data is always captured in the result.

Gate modes (config["fundamentals"]["hard_gate"])
─────────────────────────────────────────────────
    hard_gate=False (default) → passes = (conditions_met >= 4)  soft gate
    hard_gate=True            → passes = (conditions_met == 7)  hard gate

Config keys consumed
────────────────────
    config["fundamentals"]["hard_gate"]                          bool  (default False)
    config["fundamentals"]["conditions"]["min_sales_growth_yoy"] float (default 10.0)
    config["fundamentals"]["conditions"]["min_roe"]              float (default 15.0)
    config["fundamentals"]["conditions"]["max_de"]               float (default 1.0)
    config["fundamentals"]["conditions"]["min_promoter_holding"] float (default 35.0)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# FundamentalResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FundamentalResult:
    """
    Immutable result returned by check_fundamental_template().

    Fields
    ──────
    passes : bool
        True when the stock clears the fundamental gate.
        With hard_gate=False (default): conditions_met >= 4.
        With hard_gate=True:            conditions_met == 7.

    conditions_met : int
        Count of passing conditions (0–7).

    conditions : dict[str, bool]
        Per-condition boolean map: {"F1": True, "F2": False, ...}

    details : dict[str, Any]
        Raw parsed values extracted from the fundamentals dict.
        Keys: eps, eps_accelerating, sales_growth_yoy, roe,
              debt_to_equity, promoter_holding, profit_growth.
        Values are None when the underlying field was missing or
        unparseable — callers may surface these directly in reports.

    hard_fails : list[str]
        Condition codes that did NOT pass, e.g. ["F3", "F5"].
        ["no_data"] when fundamentals=None was passed in.

    fundamental_score : float
        0–100.  Computed as (conditions_met / 7) * 100.0.
        Used as the "fundamental" component in rules/scorer.py.
    """

    passes:            bool
    conditions_met:    int
    conditions:        dict[str, bool] = field(default_factory=dict)
    details:           dict[str, Any]  = field(default_factory=dict)
    hard_fails:        list[str]       = field(default_factory=list)
    fundamental_score: float           = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_TICK  = "✓"
_CROSS = "✗"

_ALL_FALSE_CONDITIONS: dict[str, bool] = {
    "eps_growth_yoy":          False,
    "eps_growth_qoq":          False,
    "revenue_growth_yoy":      False,
    "roe_positive":            False,
    "debt_to_equity_ok":       False,
    "institutional_sponsorship": False,
    "earnings_surprise":       False,
}


def _mark(passed: bool) -> str:
    return _TICK if passed else _CROSS


def _safe_float(val: Any) -> float | None:
    """
    Coerce *val* to a Python float.

    Returns None (never raises) when val is None, non-numeric, or
    otherwise unparseable.  A None return causes the corresponding
    condition to evaluate to False — this is the intended behaviour
    for missing fundamentals fields.
    """
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def check_fundamental_template(
    fundamentals: dict | None,
    config: dict,
) -> FundamentalResult:
    """
    Evaluate all 7 Minervini fundamental conditions against *fundamentals*.

    Parameters
    ──────────
    fundamentals : dict | None
        Parsed fundamentals dict from ingestion/fundamentals.py.
        Pass None to trigger the graceful-degradation path (scraper
        returned no data) — this is not an error condition.

        Expected keys (all optional — missing keys → condition=False):
            eps              (float)  Latest quarterly EPS
            eps_accelerating (bool)   True if most recent QoQ > previous QoQ
            sales_growth_yoy (float)  Year-on-year revenue growth, percent
            roe              (float)  Return on Equity, percent
            debt_to_equity   (float)  D/E ratio
            promoter_holding (float)  Promoter shareholding, percent
            profit_growth    (float)  Year-on-year profit growth

    config : dict
        Application configuration dict (loaded from settings.yaml).
        Keys consumed from config["fundamentals"]:
            hard_gate            (bool,  default False)
            conditions.min_sales_growth_yoy (float, default 10.0)
            conditions.min_roe              (float, default 15.0)
            conditions.max_de               (float, default 1.0)
            conditions.min_promoter_holding (float, default 35.0)

    Returns
    ───────
    FundamentalResult
        Always returned — this function never raises.

    Notes
    ─────
    Individual condition failures are captured in the result's
    ``conditions``, ``hard_fails``, and ``details`` fields.  They are
    never exceptions — the "fail-loud" contract is limited to the
    technical rule engine (trend_template, stage); fundamentals follow
    the "graceful-degradation" contract instead.
    """
    # ── Extract thresholds from config ────────────────────────────────────────
    fund_cfg = config.get("fundamentals", {})
    cond_cfg = fund_cfg.get("conditions", {})

    min_sales_growth_yoy: float = float(cond_cfg.get("min_sales_growth_yoy", 10.0))
    min_roe:              float = float(cond_cfg.get("min_roe",              15.0))
    max_de:               float = float(cond_cfg.get("max_de",               1.0))
    min_promoter_holding: float = float(cond_cfg.get("min_promoter_holding", 35.0))
    hard_gate:            bool  = bool(fund_cfg.get("hard_gate",             False))

    log.debug(
        "check_fundamental_template called",
        has_data=fundamentals is not None,
        hard_gate=hard_gate,
        min_sales_growth_yoy=min_sales_growth_yoy,
        min_roe=min_roe,
        max_de=max_de,
        min_promoter_holding=min_promoter_holding,
    )

    # ── Graceful-degradation path: no data available ──────────────────────────
    if fundamentals is None:
        log.warning(
            "Fundamental template: no data — returning graceful-degradation result",
            hard_gate=hard_gate,
        )
        return FundamentalResult(
            passes=False,
            conditions_met=0,
            conditions=dict(_ALL_FALSE_CONDITIONS),
            details={},
            hard_fails=["no_data"],
            fundamental_score=0.0,
        )

    # ── Extract raw values (all safe — None on any failure) ───────────────────
    eps:              float | None = _safe_float(fundamentals.get("eps"))
    eps_accelerating: Any          = fundamentals.get("eps_accelerating")
    sales_growth_yoy: float | None = _safe_float(fundamentals.get("sales_growth_yoy"))
    roe:              float | None = _safe_float(fundamentals.get("roe"))
    debt_to_equity:   float | None = _safe_float(fundamentals.get("debt_to_equity"))
    promoter_holding: float | None = _safe_float(fundamentals.get("promoter_holding"))
    profit_growth:    float | None = _safe_float(fundamentals.get("profit_growth"))

    conditions: dict[str, bool] = {}

    # ── F1: EPS positive ──────────────────────────────────────────────────────
    f1 = eps is not None and eps > 0
    conditions["eps_growth_yoy"] = f1
    log.debug(
        f"F1 EPS positive {_mark(f1)}",
        eps=eps,
        passed=f1,
    )

    # ── F2: EPS accelerating ──────────────────────────────────────────────────
    f2 = eps_accelerating is True
    conditions["eps_growth_qoq"] = f2
    log.debug(
        f"F2 EPS accelerating {_mark(f2)}",
        eps_accelerating=eps_accelerating,
        passed=f2,
    )

    # ── F3: Sales growth >= min_sales_growth_yoy % ───────────────────────────
    f3 = sales_growth_yoy is not None and sales_growth_yoy >= min_sales_growth_yoy
    conditions["revenue_growth_yoy"] = f3
    log.debug(
        f"F3 Sales growth >= {min_sales_growth_yoy:.1f}% {_mark(f3)}",
        sales_growth_yoy=sales_growth_yoy,
        threshold=min_sales_growth_yoy,
        passed=f3,
    )

    # ── F4: ROE >= min_roe % ──────────────────────────────────────────────────
    f4 = roe is not None and roe >= min_roe
    conditions["roe_positive"] = f4
    log.debug(
        f"F4 ROE >= {min_roe:.1f}% {_mark(f4)}",
        roe=roe,
        threshold=min_roe,
        passed=f4,
    )

    # ── F5: D/E ratio <= max_de ───────────────────────────────────────────────
    f5 = debt_to_equity is not None and debt_to_equity <= max_de
    conditions["debt_to_equity_ok"] = f5
    log.debug(
        f"F5 D/E ratio <= {max_de:.1f} {_mark(f5)}",
        debt_to_equity=debt_to_equity,
        threshold=max_de,
        passed=f5,
    )

    # ── F6: Promoter holding >= min_promoter_holding % ───────────────────────
    f6 = promoter_holding is not None and promoter_holding >= min_promoter_holding
    conditions["institutional_sponsorship"] = f6
    log.debug(
        f"F6 Promoter holding >= {min_promoter_holding:.1f}% {_mark(f6)}",
        promoter_holding=promoter_holding,
        threshold=min_promoter_holding,
        passed=f6,
    )

    # ── F7: Positive profit growth ────────────────────────────────────────────
    f7 = profit_growth is not None and profit_growth > 0
    conditions["earnings_surprise"] = f7
    log.debug(
        f"F7 Profit growth > 0 {_mark(f7)}",
        profit_growth=profit_growth,
        passed=f7,
    )

    # ── Aggregate ─────────────────────────────────────────────────────────────
    conditions_met:    int   = sum(conditions.values())
    hard_fails:        list  = [code for code, passed in conditions.items() if not passed]
    fundamental_score: float = (conditions_met / 7) * 100.0

    # Gate logic: hard_gate=True requires all 7; default soft gate requires >= 4
    passes: bool = (conditions_met == 7) if hard_gate else (conditions_met >= 4)

    # ── Raw values dict surfaced for reports / scorer ─────────────────────────
    details: dict[str, Any] = {
        "eps":              eps,
        "eps_accelerating": eps_accelerating,
        "sales_growth_yoy": sales_growth_yoy,
        "roe":              roe,
        "debt_to_equity":   debt_to_equity,
        "promoter_holding": promoter_holding,
        "profit_growth":    profit_growth,
    }

    result = FundamentalResult(
        passes=passes,
        conditions_met=conditions_met,
        conditions=conditions,
        details=details,
        hard_fails=hard_fails,
        fundamental_score=fundamental_score,
    )

    log.info(
        "Fundamental template evaluated",
        passes=passes,
        conditions_met=conditions_met,
        hard_gate=hard_gate,
        hard_fails=hard_fails,
        fundamental_score=round(fundamental_score, 2),
    )

    return result
