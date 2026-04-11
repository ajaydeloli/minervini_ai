"""
llm/explainer.py — Minervini AI LLM Narrative Layer (Phase 6)

Public API
----------
generate_trade_brief(result, ohlcv_tail, config) -> str | None
    Generates a concise trade brief for a single SEPAResult using the
    trade_brief.j2 template.  Returns None when LLM is disabled, the
    result does not pass the quality filter, or any error occurs.

generate_watchlist_summary(results, run_date, config) -> str | None
    Generates a market-level watchlist summary across all SEPAResults using
    the watchlist_summary.j2 template.  Returns None when LLM is disabled,
    the results list is empty, or any error occurs.
"""

from __future__ import annotations

import pandas as pd
from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from llm.llm_client import get_llm_client
from utils.logger import get_logger
from utils.exceptions import LLMError, LLMProviderError, LLMResponseError  # noqa: F401

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

log = get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "prompt_templates"

# Lazily initialised Jinja2 environment (shared across calls)
_jinja_env: Optional[Environment] = None


def _get_jinja_env() -> Environment:
    """Return (and cache) the module-level Jinja2 environment."""
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
    return _jinja_env


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_trade_brief_context(
    result: "SEPAResult",  # forward ref; rules/scorer.py not imported here
    ohlcv_tail: pd.DataFrame,
) -> dict:
    """Build the Jinja2 template context dict for the trade_brief template.

    None values are passed through as-is; the template is responsible for
    rendering "N/A" where appropriate.

    Parameters
    ----------
    result:
        The scored SEPAResult for a single symbol.
    ohlcv_tail:
        A recent slice of OHLCV data (any length ≥ 1).  Must contain at
        minimum a ``close``, ``high``, and ``low`` column.

    Returns
    -------
    dict
        Context ready to be fed into Environment.get_template().render().
    """
    # -- OHLCV-derived fields ------------------------------------------------
    recent_close: Optional[float] = None
    recent_high_52w: Optional[float] = None
    recent_low_52w: Optional[float] = None

    if not ohlcv_tail.empty:
        try:
            recent_close = float(ohlcv_tail["close"].iloc[-1])
            recent_high_52w = float(ohlcv_tail["high"].max())
            recent_low_52w = float(ohlcv_tail["low"].min())
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            log.warning(
                "explainer: could not extract OHLCV fields for %s — %s",
                result.symbol,
                exc,
            )

    # -- VCP details ---------------------------------------------------------
    vcp_details: dict = result.vcp_details or {}
    vcp_contraction_count: Optional[int] = vcp_details.get("contraction_count")
    vcp_final_depth_pct: Optional[float] = vcp_details.get("final_depth_pct")

    # -- Fundamental details -------------------------------------------------
    fund_details: dict = result.fundamental_details or {}
    conditions_met_fund: Optional[int] = fund_details.get("conditions_met")

    return {
        # Identity
        "symbol": result.symbol,
        "date": result.date,
        # Stage
        "stage": result.stage,
        "stage_label": result.stage_label,
        "stage_confidence": result.stage_confidence,
        # Trend template
        "trend_template_pass": result.trend_template_pass,
        "conditions_met": result.conditions_met,
        # VCP
        "vcp_qualified": result.vcp_qualified,
        "vcp_grade": result.vcp_grade,
        "vcp_contraction_count": vcp_contraction_count,
        "vcp_final_depth_pct": vcp_final_depth_pct,
        # Breakout / trade levels
        "breakout_triggered": result.breakout_triggered,
        "entry_price": result.entry_price,
        "stop_loss": result.stop_loss,
        "risk_pct": result.risk_pct,
        "rr_ratio": result.rr_ratio,
        # Ratings
        "rs_rating": result.rs_rating,
        "setup_quality": result.setup_quality,
        "score": result.score,
        # Fundamentals
        "fundamental_pass": result.fundamental_pass,
        "conditions_met_fund": conditions_met_fund,
        "roe": fund_details.get("roe"),
        "debt_to_equity": fund_details.get("debt_to_equity"),
        "eps_accelerating": fund_details.get("eps_accelerating"),
        "sales_growth_yoy": fund_details.get("sales_growth_yoy"),
        "promoter_holding": fund_details.get("promoter_holding"),
        # News
        "news_score": result.news_score,
        # OHLCV
        "recent_close": recent_close,
        "recent_high_52w": recent_high_52w,
        "recent_low_52w": recent_low_52w,
    }


def _build_watchlist_summary_context(
    results: list["SEPAResult"],
    run_date: date,
) -> dict:
    """Build the Jinja2 template context dict for the watchlist_summary template.

    Parameters
    ----------
    results:
        All SEPAResults for the current run (may include any quality tier).
    run_date:
        The date the screening run was performed.

    Returns
    -------
    dict
        Context ready to be fed into Environment.get_template().render().
    """
    # Top symbols: A+ and A, sorted by score descending, capped at 5
    top_symbols = sorted(
        [r for r in results if r.setup_quality in ("A+", "A")],
        key=lambda r: r.score,
        reverse=True,
    )[:5]

    # Market breadth based on Stage-2 pass count across ALL results
    stage2_count = sum(1 for r in results if r.stage == 2)
    if stage2_count > 10:
        market_breadth = "broad"
    elif stage2_count < 3:
        market_breadth = "narrow"
    else:
        market_breadth = "mixed"

    # Aggregate counts
    total_screened = len(results)
    a_plus_count = sum(1 for r in results if r.setup_quality == "A+")
    a_count = sum(1 for r in results if r.setup_quality == "A")
    b_count = sum(1 for r in results if r.setup_quality == "B")
    fail_count = sum(1 for r in results if r.setup_quality == "FAIL")
    breakout_count = sum(1 for r in results if r.breakout_triggered)

    return {
        "run_date": run_date,
        "total_screened": total_screened,
        "stage2_count": stage2_count,
        "market_breadth": market_breadth,
        "a_plus_count": a_plus_count,
        "a_count": a_count,
        "b_count": b_count,
        "fail_count": fail_count,
        "breakout_count": breakout_count,
        "top_symbols": [
            {
                "symbol": r.symbol,
                "setup_quality": r.setup_quality,
                "score": r.score,
                "rs_rating": r.rs_rating,
                "stage_label": r.stage_label,
                "vcp_qualified": r.vcp_qualified,
                "breakout_triggered": r.breakout_triggered,
                "entry_price": r.entry_price,
                "stop_loss": r.stop_loss,
                "rr_ratio": r.rr_ratio,
            }
            for r in top_symbols
        ],
    }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def generate_trade_brief(
    result: "SEPAResult",
    ohlcv_tail: pd.DataFrame,
    config: dict,
) -> str | None:
    """Generate a plain-text trade brief for a single SEPA result via the LLM.

    Parameters
    ----------
    result:
        The SEPAResult to explain.
    ohlcv_tail:
        Recent OHLCV rows used to extract price context (52-week range, last
        close).  An empty DataFrame is handled gracefully — price fields will
        be None and a warning will be emitted.
    config:
        Full application config dict (must contain the ``llm`` sub-section).

    Returns
    -------
    str | None
        The stripped LLM response, or None on any early-exit or error.
    """
    llm_cfg: dict = config.get("llm", {})

    # 1. LLM globally disabled — silent return
    if not llm_cfg.get("enabled", False):
        return None

    # 2. Quality filter — silent return
    only_for_quality: list[str] = llm_cfg.get("only_for_quality", [])
    if result.setup_quality not in only_for_quality:
        return None

    # 3. Warn and return if ohlcv_tail is empty
    if ohlcv_tail.empty:
        log.warning(
            "explainer: empty ohlcv_tail for %s — skipping trade brief generation",
            result.symbol,
        )
        return None

    # 4. Acquire LLM client
    client = get_llm_client(config)
    if client is None:
        return None

    try:
        # 5. Build context and render template
        context = _build_trade_brief_context(result, ohlcv_tail)
        env = _get_jinja_env()
        template = env.get_template("trade_brief.j2")
        prompt = template.render(**context)

        # 6. Call the LLM
        max_tokens: int = llm_cfg.get("max_tokens", 350)
        response: str = client.complete(prompt, max_tokens=max_tokens)

        log.info(
            "explainer: trade brief generated for %s via %s (%d chars)",
            result.symbol,
            llm_cfg.get("provider", "unknown"),
            len(response),
        )
        return response.strip()

    except LLMError as exc:
        log.warning(
            "explainer: LLM error generating trade brief for %s — %s",
            result.symbol,
            exc,
        )
        return None
    except Exception as exc:  # pylint: disable=broad-except
        log.warning(
            "explainer: unexpected error generating trade brief for %s — %s",
            result.symbol,
            exc,
        )
        return None


def generate_watchlist_summary(
    results: list["SEPAResult"],
    run_date: date,
    config: dict,
) -> str | None:
    """Generate a plain-text watchlist summary for the current screening run.

    Parameters
    ----------
    results:
        All SEPAResults produced by the current run.
    run_date:
        Date the screening run was executed.
    config:
        Full application config dict (must contain the ``llm`` sub-section).

    Returns
    -------
    str | None
        The stripped LLM response, or None on any early-exit or error.
    """
    llm_cfg: dict = config.get("llm", {})

    # 1. LLM globally disabled — silent return
    if not llm_cfg.get("enabled", False):
        return None

    # 2. Nothing to summarise — silent return
    if not results:
        return None

    # 3. Acquire LLM client
    client = get_llm_client(config)
    if client is None:
        return None

    try:
        # 4. Build context and render template
        context = _build_watchlist_summary_context(results, run_date)
        env = _get_jinja_env()
        template = env.get_template("watchlist_summary.j2")
        prompt = template.render(**context)

        # 5. Call the LLM
        max_tokens: int = llm_cfg.get("max_tokens", 350)
        response: str = client.complete(prompt, max_tokens=max_tokens)

        log.info(
            "explainer: watchlist summary generated via %s (%d chars, %d results)",
            llm_cfg.get("provider", "unknown"),
            len(response),
            len(results),
        )
        return response.strip()

    except LLMError as exc:
        log.warning(
            "explainer: LLM error generating watchlist summary — %s",
            exc,
        )
        return None
    except Exception as exc:  # pylint: disable=broad-except
        log.warning(
            "explainer: unexpected error generating watchlist summary — %s",
            exc,
        )
        return None
