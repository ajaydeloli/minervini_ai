"""
dashboard/components/metrics.py
────────────────────────────────
Reusable Streamlit widget components for the Minervini AI dashboard.

Provides score gauges, KPI cards, checklist grids, and summary panels
that are shared across multiple dashboard pages (screener, paper trading,
fundamentals, VCP analysis).

Public API
──────────
    render_score_gauge(score, quality, label)
    render_trend_template_checklist(conditions, conditions_met)
    render_fundamental_scorecard(fundamental_details, fundamental_pass)
    render_vcp_summary(vcp_details, vcp_qualified)
    render_run_summary_kpis(run_meta)
    render_portfolio_kpis(summary)
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_BADGE_CSS: dict[str, str] = {
    "A+":   "background:#FFD700;color:#000;padding:2px 10px;border-radius:6px;font-weight:700;",
    "A":    "background:#28a745;color:#fff;padding:2px 10px;border-radius:6px;font-weight:700;",
    "B":    "background:#ffc107;color:#000;padding:2px 10px;border-radius:6px;font-weight:700;",
    "C":    "background:#6c757d;color:#fff;padding:2px 10px;border-radius:6px;font-weight:700;",
    "FAIL": "background:#dc3545;color:#fff;padding:2px 10px;border-radius:6px;font-weight:700;",
}

_QUALITY_BADGE_DEFAULT = "background:#6c757d;color:#fff;padding:2px 10px;border-radius:6px;font-weight:700;"


def _quality_badge_html(quality: str) -> str:
    css = _QUALITY_BADGE_CSS.get(str(quality).upper(), _QUALITY_BADGE_DEFAULT)
    return f'<span style="{css}">{quality}</span>'


def _check_icon(passed: bool) -> str:
    return "✅" if passed else "❌"


def _colour_text(text: str, colour: str) -> str:
    return f'<span style="color:{colour};font-weight:600;">{text}</span>'


# ─────────────────────────────────────────────────────────────────────────────
# 1. Score Gauge
# ─────────────────────────────────────────────────────────────────────────────

def render_score_gauge(
    score: int,
    quality: str,
    label: str = "SEPA Score",
) -> None:
    """
    Render a visual 0–100 score gauge.

    Uses st.metric for the numeric display, st.progress for the bar,
    and a coloured quality badge below.

    Parameters
    ──────────
    score   : Integer 0–100.
    quality : Setup quality tag — one of A+, A, B, C, FAIL.
    label   : Header label shown above the metric (default "SEPA Score").
    """
    if score is None:
        score = 0
    score = max(0, min(100, int(score)))
    quality = str(quality) if quality else "FAIL"

    st.metric(label=label, value=f"{score} / 100")
    st.progress(score / 100)
    st.markdown(
        f"Quality: &nbsp; {_quality_badge_html(quality)}",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Trend Template Checklist
# ─────────────────────────────────────────────────────────────────────────────

_TT_CONDITION_LABELS: dict[str, tuple[str, str]] = {
    "condition_1": ("Price > SMA150 & SMA200",      "Close above both long-term MAs"),
    "condition_2": ("SMA150 > SMA200",               "150-day MA above 200-day MA"),
    "condition_3": ("SMA200 trending up (20d)",      "200-day MA rising for ≥ 20 days"),
    "condition_4": ("SMA50 > SMA150 & SMA200",       "50-day MA above both longer MAs"),
    "condition_5": ("Price > SMA50",                  "Close above the 50-day MA"),
    "condition_6": ("Price ≥ 25% above 52w low",     "At least 25% off the 52-week low"),
    "condition_7": ("Price within 25% of 52w high",  "Within 25% of the 52-week high"),
    "condition_8": ("RS Rating ≥ 70",                "Relative strength rating ≥ 70"),
}


def render_trend_template_checklist(
    conditions: Optional[dict[str, bool]],
    conditions_met: int,
) -> None:
    """
    Render the 8 Minervini Trend Template conditions as a two-column grid.

    Parameters
    ──────────
    conditions     : Dict mapping condition_1..condition_8 → bool.
                     None or empty → shows a warning and returns.
    conditions_met : Integer count of passing conditions (used in summary).
    """
    if not conditions:
        st.warning("Trend Template data not available.")
        return

    conditions_met = int(conditions_met) if conditions_met is not None else 0

    # Summary badge
    if conditions_met == 8:
        colour = "#28a745"
    elif conditions_met >= 6:
        colour = "#ffc107"
    else:
        colour = "#dc3545"

    st.markdown(
        f"{_colour_text(f'{conditions_met}/8 conditions passing', colour)}",
        unsafe_allow_html=True,
    )
    st.divider()

    keys = [f"condition_{i}" for i in range(1, 9)]
    left_keys = keys[:4]
    right_keys = keys[4:]

    col_left, col_right = st.columns(2)

    def _render_condition(col: st.delta_generator.DeltaGenerator, key: str) -> None:
        label, desc = _TT_CONDITION_LABELS.get(key, (key, ""))
        passed = bool(conditions.get(key, False))
        icon = _check_icon(passed)
        txt_colour = "#28a745" if passed else "#dc3545"
        col.markdown(
            f"{icon} &nbsp; {_colour_text(label, txt_colour)}<br>"
            f"<small style='color:#888;margin-left:24px;'>{desc}</small>",
            unsafe_allow_html=True,
        )

    with col_left:
        for k in left_keys:
            _render_condition(col_left, k)

    with col_right:
        for k in right_keys:
            _render_condition(col_right, k)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Fundamental Scorecard
# ─────────────────────────────────────────────────────────────────────────────

_FUND_CONDITION_LABELS: dict[str, str] = {
    "eps_positive":     "EPS Positive",
    "eps_accelerating": "EPS Accelerating (QoQ)",
    "sales_growth":     "Sales Growth ≥ 10% YoY",
    "roe":              "ROE ≥ 15%",
    "de_ratio":         "D/E Ratio ≤ 1.0",
    "promoter_holding": "Promoter Holding ≥ 35%",
    "profit_growth":    "Profit Growth Positive",
}


def render_fundamental_scorecard(
    fundamental_details: Optional[dict[str, bool]],
    fundamental_pass: bool,
) -> None:
    """
    Render the 7 Minervini fundamental conditions as a checklist.

    Shows an overall pass/fail badge at the top.  Gracefully handles
    None or empty fundamental_details.

    Parameters
    ──────────
    fundamental_details : Dict mapping condition keys → bool.
    fundamental_pass    : Overall fundamental pass/fail boolean.
    """
    if fundamental_pass:
        badge_html = _quality_badge_html("A")
        badge_label = "FUNDAMENTALS PASS"
        badge_css = "background:#28a745;color:#fff;padding:3px 12px;border-radius:6px;font-weight:700;"
    else:
        badge_css = "background:#dc3545;color:#fff;padding:3px 12px;border-radius:6px;font-weight:700;"
        badge_label = "FUNDAMENTALS FAIL"

    st.markdown(
        f'<span style="{badge_css}">{badge_label}</span>',
        unsafe_allow_html=True,
    )
    st.divider()

    if not fundamental_details:
        st.info("Fundamental details not available.")
        return

    for key, label in _FUND_CONDITION_LABELS.items():
        passed = bool(fundamental_details.get(key, False))
        icon = _check_icon(passed)
        colour = "#28a745" if passed else "#dc3545"
        st.markdown(
            f"{icon} &nbsp; {_colour_text(label, colour)}",
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. VCP Summary
# ─────────────────────────────────────────────────────────────────────────────

def render_vcp_summary(
    vcp_details: Optional[dict],
    vcp_qualified: bool,
) -> None:
    """
    Render a compact VCP metrics panel using st.metric.

    Displays grade badge, contraction stats, volume ratio, base length,
    and tightness.  Shows a warning banner when VCP is not qualified.

    Parameters
    ──────────
    vcp_details   : Dict from SEPAResult.vcp_details (may be None/empty).
    vcp_qualified : Boolean — whether the VCP pattern qualifies.
    """
    if not vcp_qualified:
        st.warning("⚠️ VCP Not Qualified — pattern does not meet entry criteria.")

    if not vcp_details:
        st.info("VCP details not available.")
        return

    grade = str(vcp_details.get("quality_grade") or "N/A")
    grade_upper = grade.upper()
    grade_css = _QUALITY_BADGE_CSS.get(grade_upper, _QUALITY_BADGE_DEFAULT)
    st.markdown(
        f"VCP Grade: &nbsp;<span style='{grade_css}'>{grade}</span>",
        unsafe_allow_html=True,
    )
    st.divider()

    col1, col2, col3 = st.columns(3)

    contraction_count = vcp_details.get("contraction_count")
    max_depth = vcp_details.get("max_depth_pct")
    final_depth = vcp_details.get("final_depth_pct")
    vol_ratio = vcp_details.get("vol_ratio")
    base_weeks = vcp_details.get("base_weeks")
    fail_reason = vcp_details.get("fail_reason")

    def _fmt_pct(v) -> str:
        return f"{float(v):.1f}%" if v is not None else "—"

    def _fmt_ratio(v) -> str:
        return f"{float(v):.2f}x" if v is not None else "—"

    def _fmt_int(v) -> str:
        return str(int(v)) if v is not None else "—"

    def _fmt_weeks(v) -> str:
        return f"{float(v):.1f} wk" if v is not None else "—"

    col1.metric("Contractions",      _fmt_int(contraction_count))
    col2.metric("Deepest Retrace",   _fmt_pct(max_depth))
    col3.metric("Final Contraction", _fmt_pct(final_depth))

    col4, col5 = st.columns(2)
    col4.metric("Volume Ratio (last/first)", _fmt_ratio(vol_ratio))
    col5.metric("Base Length",               _fmt_weeks(base_weeks))

    if fail_reason:
        st.caption(f"⚠️ Fail reason: {fail_reason}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Run Summary KPIs
# ─────────────────────────────────────────────────────────────────────────────

def render_run_summary_kpis(run_meta: Optional[dict]) -> None:
    """
    Render 4 KPI cards in a row for the daily screener run.

    Cards
    ─────
    1. Universe scanned  (int)
    2. Stage 2 passed    (int) + delta vs yesterday
    3. A+/A setups       (int)
    4. Last run time     (formatted IST string)

    Parameters
    ──────────
    run_meta : Dict from storage (run_history row).  Expected keys:
               universe_size, stage2_count, stage2_count_prev,
               ap_a_count, last_run_at_ist.
               None → shows empty / zero cards gracefully.
    """
    meta = run_meta or {}

    universe      = meta.get("universe_size", 0) or 0
    stage2        = meta.get("stage2_count", 0) or 0
    stage2_prev   = meta.get("stage2_count_prev", None)
    ap_a          = meta.get("ap_a_count", 0) or 0
    last_run      = meta.get("last_run_at_ist", "—") or "—"

    stage2_delta = None
    if stage2_prev is not None:
        try:
            stage2_delta = int(stage2) - int(stage2_prev)
        except (TypeError, ValueError):
            stage2_delta = None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Universe Scanned", f"{int(universe):,}")
    col2.metric(
        "Stage 2 Passed",
        f"{int(stage2):,}",
        delta=str(stage2_delta) if stage2_delta is not None else None,
    )
    col3.metric("A+ / A Setups", f"{int(ap_a):,}")
    col4.metric("Last Run (IST)", str(last_run))


# ─────────────────────────────────────────────────────────────────────────────
# 6. Portfolio KPIs
# ─────────────────────────────────────────────────────────────────────────────

def render_portfolio_kpis(summary: Optional[dict]) -> None:
    """
    Render 5 paper-trading KPI cards in a row.

    Cards
    ─────
    1. Total Value      (₹)
    2. Total Return     (%)
    3. Win Rate         (%)
    4. Open Positions   (int)
    5. Realised P&L     (₹)

    Positive returns / P&L are green; negative are red via st.metric delta.

    Parameters
    ──────────
    summary : Dict or PortfolioSummary-like object.  Expected keys:
              total_value, total_return_pct, win_rate,
              open_trades, realised_pnl.
              None → shows zero cards gracefully.
    """
    if summary is None:
        summary = {}

    # Accept both dict and dataclass (PortfolioSummary)
    def _get(key: str, default=0):
        if isinstance(summary, dict):
            return summary.get(key, default)
        return getattr(summary, key, default)

    total_value     = float(_get("total_value", 0))
    total_return_pct = float(_get("total_return_pct", 0))
    win_rate        = float(_get("win_rate", 0))
    open_trades     = int(_get("open_trades", 0))
    realised_pnl    = float(_get("realised_pnl", 0))

    def _inr(v: float) -> str:
        return f"₹{v:,.0f}"

    def _signed_delta(v: float) -> str:
        return f"+{v:,.2f}" if v >= 0 else f"{v:,.2f}"

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Total Value", _inr(total_value))

    col2.metric(
        "Total Return",
        f"{total_return_pct:+.2f}%",
        delta=f"{total_return_pct:+.2f}%",
        delta_color="normal",
    )

    col3.metric("Win Rate", f"{win_rate:.1f}%")

    col4.metric("Open Positions", str(open_trades))

    col5.metric(
        "Realised P&L",
        _inr(realised_pnl),
        delta=_signed_delta(realised_pnl),
        delta_color="normal",
    )
