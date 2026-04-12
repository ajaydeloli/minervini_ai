"""
dashboard/pages/02_Screener.py
──────────────────────────────
Full universe screening results with interactive filters.

Design doc: "Full universe table with quality/stage/RS filters + export CSV button"

Sections
────────
  1. Page header + date selector
  2. Filter panel  (quality, stage, RS, VCP, TT, score; reset button)
  3. Results table (filtered, with watchlist badges + stock page nav links)
  4. Export CSV + collapsible screener statistics
  5. Sector breakdown (optional — requires data/metadata/symbol_info.csv)

Data
────
  All rows are loaded once from the ``sepa_results`` SQLite table for the
  selected date using screener.results.load_results().  Filtering is done
  in-memory on the cached DataFrame — no re-query on every widget change.
  DB reads are cached with @st.cache_data(ttl=300).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Project root on sys.path  (dashboard/pages/ → dashboard/ → project root)
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DB_PATH = str(_PROJECT_ROOT / "data" / "minervini.db")
_SYMBOL_INFO_PATH = _PROJECT_ROOT / "data" / "metadata" / "symbol_info.csv"

_QUALITY_ORDER = ["A+", "A", "B", "C", "FAIL"]
_QUALITY_DEFAULT = ["A+", "A", "B"]
_STAGE_DEFAULT = [2]

# ─────────────────────────────────────────────────────────────────────────────
# Cached data-loading helpers
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _load_distinct_dates(db_path: str) -> list[str]:
    """
    Return all distinct screen dates from sepa_results, newest first.
    Returns an empty list when the table does not exist.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        rows = conn.execute(
            "SELECT DISTINCT date FROM sepa_results ORDER BY date DESC"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


@st.cache_data(ttl=300)
def _load_sepa_data(db_path: str, run_date: str) -> list[dict]:
    """
    Load all sepa_results rows for run_date, sorted by score descending.
    Uses screener.results.load_results() — the canonical query helper.
    Falls back gracefully on import/DB errors.
    """
    try:
        from screener.results import load_results
        return load_results(db_path, run_date=run_date)
    except Exception as exc:
        st.error(f"Could not load sepa_results: {exc}")
        return []



@st.cache_data(ttl=300)
def _get_watchlist_symbols(db_path: str) -> set[str]:
    """
    Return the set of symbols in the watchlist table.
    Returns an empty set on error.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        rows = conn.execute("SELECT symbol FROM watchlist").fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


# ─────────────────────────────────────────────────────────────────────────────
# Filter defaults — stored in session state so Reset button can restore them
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULTS: dict = {
    "scr_quality":  ["A+", "A", "B"],
    "scr_stage":    [2],
    "scr_min_rs":   60,
    "scr_vcp_only": False,
    "scr_tt_only":  False,
    "scr_min_score": 40,
}


def _init_session_state() -> None:
    """Seed session state keys with defaults on first load."""
    for key, val in _DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _reset_filters() -> None:
    """Restore all filter session-state keys to their defaults."""
    for key, val in _DEFAULTS.items():
        st.session_state[key] = val


# ─────────────────────────────────────────────────────────────────────────────
# In-memory filter application
# ─────────────────────────────────────────────────────────────────────────────

def _apply_filters(
    df: pd.DataFrame,
    quality_sel: list[str],
    stage_sel: list[int],
    min_rs: int,
    vcp_only: bool,
    tt_only: bool,
    min_score: int,
) -> pd.DataFrame:
    """Apply all active filter criteria to the cached DataFrame."""
    mask = pd.Series([True] * len(df), index=df.index)

    if quality_sel and "setup_quality" in df.columns:
        mask &= df["setup_quality"].isin(quality_sel)

    if stage_sel and "stage" in df.columns:
        mask &= df["stage"].isin(stage_sel)

    if min_rs > 0 and "rs_rating" in df.columns:
        mask &= df["rs_rating"].fillna(0) >= min_rs

    if vcp_only and "vcp_qualified" in df.columns:
        mask &= df["vcp_qualified"].astype(bool)

    if tt_only and "trend_template_pass" in df.columns:
        mask &= df["trend_template_pass"].astype(bool)

    if min_score > 0 and "score" in df.columns:
        mask &= df["score"].fillna(0) >= min_score

    return df[mask].reset_index(drop=True)



# ─────────────────────────────────────────────────────────────────────────────
# Page bootstrap
# ─────────────────────────────────────────────────────────────────────────────

_init_session_state()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Page header + date selector
# ══════════════════════════════════════════════════════════════════════════════

st.title("🔍 Full Screener")
st.markdown(
    "<p style='color:#8b949e; margin-top:-0.5rem;'>"
    "Universe-wide SEPA screening results with live filters and export.</p>",
    unsafe_allow_html=True,
)

available_dates = _load_distinct_dates(_DB_PATH)

if not available_dates:
    st.warning(
        "⚠️ No screen results found in the database. "
        "Run the pipeline first: `python scripts/run_daily.py --date today`"
    )
    st.stop()

# Date selector — defaults to latest date
_date_options = available_dates                # already newest-first
_default_date = _date_options[0]

col_date, col_count = st.columns([2, 3])

with col_date:
    selected_date = st.selectbox(
        "📅 Screen date",
        options=_date_options,
        index=0,
        help="Choose a past screen date to view its results.",
    )

# Load raw data for selected date (cached)
raw_data = _load_sepa_data(_DB_PATH, selected_date)
total_count = len(raw_data)

with col_count:
    st.markdown(
        f"<div style='padding-top:1.9rem; color:#8b949e; font-size:0.92rem;'>"
        f"📊 Showing <b style='color:#e6edf3;'>{total_count:,}</b> "
        f"symbols screened on <b style='color:#e6edf3;'>{selected_date}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Filter panel
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("🎛️ Filters", expanded=True):
    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns(5)

    with f_col1:
        quality_sel = st.multiselect(
            "Quality",
            options=_QUALITY_ORDER,
            default=st.session_state["scr_quality"],
            key="scr_quality",
            help="Setup quality tiers produced by the SEPA scorer.",
        )

    with f_col2:
        stage_sel = st.multiselect(
            "Stage",
            options=[1, 2, 3, 4],
            default=st.session_state["scr_stage"],
            key="scr_stage",
            help="Weinstein stage classification. Stage 2 = only buy stage.",
        )

    with f_col3:
        min_rs = st.slider(
            "Min RS Rating",
            min_value=0,
            max_value=99,
            value=st.session_state["scr_min_rs"],
            key="scr_min_rs",
            help="Minervini RS Rating (0–99). Stocks scoring below threshold are hidden.",
        )

    with f_col4:
        vcp_only = st.checkbox(
            "VCP Qualified only",
            value=st.session_state["scr_vcp_only"],
            key="scr_vcp_only",
            help="Show only stocks with a valid Volatility Contraction Pattern.",
        )
        tt_only = st.checkbox(
            "All 8 TT conditions only",
            value=st.session_state["scr_tt_only"],
            key="scr_tt_only",
            help="Require all 8 Minervini Trend Template conditions to pass.",
        )

    with f_col5:
        min_score = st.slider(
            "Min Score",
            min_value=0,
            max_value=100,
            value=st.session_state["scr_min_score"],
            key="scr_min_score",
            help="Composite SEPA score threshold (0–100).",
        )

    btn_col, cnt_col = st.columns([1, 4])
    with btn_col:
        if st.button("🔄 Reset Filters", on_click=_reset_filters):
            st.rerun()


# ─── Apply filters in-memory ──────────────────────────────────────────────────

raw_df = pd.DataFrame(raw_data) if raw_data else pd.DataFrame()

if raw_df.empty:
    st.warning("No symbols match your filters.")
    st.stop()

filtered_df = _apply_filters(
    raw_df,
    quality_sel=quality_sel,
    stage_sel=stage_sel,
    min_rs=min_rs,
    vcp_only=vcp_only,
    tt_only=tt_only,
    min_score=min_score,
)

with cnt_col:
    _match_color = "#22c55e" if len(filtered_df) > 0 else "#ef4444"
    st.markdown(
        f"<div style='padding-top:0.3rem; font-size:0.92rem;'>"
        f"<span style='color:{_match_color}; font-weight:700;'>{len(filtered_df):,}</span>"
        f"<span style='color:#8b949e;'> symbols match filters</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Results table
# ══════════════════════════════════════════════════════════════════════════════

watchlist_symbols = _get_watchlist_symbols(_DB_PATH)
filtered_results = filtered_df.to_dict("records")

if not filtered_results:
    st.warning("⚠️ No symbols match your filters. Try relaxing the criteria above.")
else:
    # ── Stock page navigation helper ─────────────────────────────────────────
    # Render clickable symbol links ABOVE the table so users can navigate.
    if len(filtered_results) <= 50:
        st.markdown(
            "<p style='color:#8b949e; font-size:0.82rem; margin-bottom:0.3rem;'>"
            "📈 Click a symbol to open its detail page:</p>",
            unsafe_allow_html=True,
        )
        link_cols = st.columns(min(10, len(filtered_results)))
        for idx, row in enumerate(filtered_results[:50]):
            sym = row.get("symbol", "")
            with link_cols[idx % 10]:
                st.page_link(
                    "pages/03_Stock.py",
                    label=sym,
                    icon="📈",
                )
        st.markdown("")

    # ── Styled results table via component ──────────────────────────────────
    try:
        from dashboard.components.tables import render_sepa_results_table
        render_sepa_results_table(
            results=filtered_results,
            watchlist_symbols=watchlist_symbols,
        )
    except ImportError:
        # Graceful fallback: plain st.dataframe if component not yet built
        _display_cols = [
            "symbol", "setup_quality", "score", "stage",
            "rs_rating", "vcp_qualified", "trend_template_pass",
            "entry_price", "stop_loss", "risk_pct", "rr_ratio",
        ]
        _visible = [c for c in _display_cols if c in filtered_df.columns]
        st.dataframe(
            filtered_df[_visible].style.format(
                {
                    "score": "{:.1f}",
                    "rs_rating": "{:.0f}",
                    "entry_price": "₹{:,.2f}",
                    "stop_loss": "₹{:,.2f}",
                    "risk_pct": "{:.1f}%",
                    "rr_ratio": "{:.2f}",
                },
                na_rep="—",
            ),
            use_container_width=True,
            hide_index=True,
        )
    except Exception as exc:
        st.error(f"Could not render results table: {exc}")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Export CSV + Screener Statistics
# ══════════════════════════════════════════════════════════════════════════════

export_col, _spacer = st.columns([1, 4])

with export_col:
    if not filtered_df.empty:
        csv_bytes = filtered_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export CSV",
            data=csv_bytes,
            file_name=f"sepa_screener_{selected_date}.csv",
            mime="text/csv",
            help="Download all columns for the currently filtered result set.",
        )

# ── Screener Statistics expander ─────────────────────────────────────────────

with st.expander("📊 Screener Statistics"):
    if raw_df.empty:
        st.info("No data to display statistics for.")
    else:
        # ── Funnel stats ─────────────────────────────────────────────────────
        _total = len(raw_df)

        def _count(col: str, truthy: bool = True) -> int:
            if col not in raw_df.columns:
                return 0
            if truthy:
                return int(raw_df[col].astype(bool).sum())
            return int((~raw_df[col].astype(bool)).sum())

        def _count_quality(q: str) -> int:
            if "setup_quality" not in raw_df.columns:
                return 0
            return int((raw_df["setup_quality"] == q).sum())

        _stage2   = int((raw_df["stage"] == 2).sum()) if "stage" in raw_df.columns else 0
        _tt_pass  = _count("trend_template_pass")
        _vcp      = _count("vcp_qualified")
        _aplus_a  = _count_quality("A+") + _count_quality("A")

        st.markdown("#### 🔽 Screening Funnel")
        st.markdown(
            f"**Universe:** `{_total:,}`"
            f" &nbsp;→&nbsp; **Stage 2:** `{_stage2:,}`"
            f" &nbsp;→&nbsp; **TT Pass:** `{_tt_pass:,}`"
            f" &nbsp;→&nbsp; **VCP:** `{_vcp:,}`"
            f" &nbsp;→&nbsp; **A+/A:** `{_aplus_a:,}`"
        )

        st.markdown("---")
        stat_col1, stat_col2 = st.columns(2)

        # ── Quality breakdown bar chart ───────────────────────────────────────
        with stat_col1:
            st.markdown("#### Quality Breakdown")
            if "setup_quality" in raw_df.columns:
                quality_counts = (
                    raw_df["setup_quality"]
                    .value_counts()
                    .reindex(_QUALITY_ORDER, fill_value=0)
                )
                quality_df = pd.DataFrame(
                    {"Quality": quality_counts.index, "Count": quality_counts.values}
                ).set_index("Quality")
                try:
                    import plotly.graph_objects as go
                    fig_q = go.Figure(
                        go.Bar(
                            x=quality_df.index.tolist(),
                            y=quality_df["Count"].tolist(),
                            marker_color=["#FFD700", "#22c55e", "#f59e0b", "#6b7280", "#ef4444"],
                            text=quality_df["Count"].tolist(),
                            textposition="outside",
                        )
                    )
                    fig_q.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#e6edf3",
                        margin=dict(t=10, b=10),
                        height=260,
                        xaxis=dict(showgrid=False),
                        yaxis=dict(showgrid=True, gridcolor="#30363d"),
                    )
                    st.plotly_chart(fig_q, use_container_width=True)
                except ImportError:
                    st.bar_chart(quality_df)
            else:
                st.info("setup_quality column not available.")

        # ── RS Rating distribution histogram ─────────────────────────────────
        with stat_col2:
            st.markdown("#### RS Rating Distribution")
            if "rs_rating" in raw_df.columns:
                rs_series = raw_df["rs_rating"].dropna()
                try:
                    import plotly.express as px
                    fig_rs = px.histogram(
                        rs_series,
                        nbins=20,
                        labels={"value": "RS Rating", "count": "Count"},
                        color_discrete_sequence=["#3b82f6"],
                    )
                    fig_rs.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#e6edf3",
                        margin=dict(t=10, b=10),
                        height=260,
                        showlegend=False,
                        xaxis=dict(title="RS Rating", showgrid=False),
                        yaxis=dict(title="Count", showgrid=True, gridcolor="#30363d"),
                    )
                    st.plotly_chart(fig_rs, use_container_width=True)
                except ImportError:
                    rs_hist = rs_series.value_counts(bins=20).sort_index()
                    st.bar_chart(rs_hist)
            else:
                st.info("rs_rating column not available.")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Sector breakdown (optional — requires symbol_info.csv)
# ══════════════════════════════════════════════════════════════════════════════

if _SYMBOL_INFO_PATH.exists():
    with st.expander("🏭 Sector Breakdown"):
        try:
            symbol_info = pd.read_csv(_SYMBOL_INFO_PATH)

            # Normalise column names to lowercase for robustness
            symbol_info.columns = symbol_info.columns.str.lower().str.strip()

            # Expect at minimum: symbol, sector
            if "symbol" not in symbol_info.columns or "sector" not in symbol_info.columns:
                st.info(
                    "symbol_info.csv must have 'symbol' and 'sector' columns. "
                    "Sector breakdown skipped."
                )
            else:
                symbol_info["symbol"] = symbol_info["symbol"].str.upper().str.strip()

                # Join filtered results with sector metadata
                results_df = filtered_df.copy() if not filtered_df.empty else raw_df.copy()
                if "symbol" in results_df.columns:
                    merged = results_df.merge(
                        symbol_info[["symbol", "sector"]],
                        on="symbol",
                        how="left",
                    )

                    def _safe_sum(series: "pd.Series") -> int:
                        return int((series == "A+").sum())

                    def _safe_a_sum(series: "pd.Series") -> int:
                        return int((series == "A").sum())

                    sector_grp = (
                        merged.groupby("sector", dropna=False)
                        .agg(
                            symbols=("symbol", "count"),
                            aplus_count=("setup_quality", _safe_sum),
                            a_count=("setup_quality", _safe_a_sum),
                            avg_score=("score", "mean"),
                        )
                        .reset_index()
                        .rename(columns={"sector": "Sector", "symbols": "Symbols",
                                         "aplus_count": "A+", "a_count": "A",
                                         "avg_score": "Avg Score"})
                        .sort_values("A+", ascending=False)
                    )

                    sector_grp["Avg Score"] = sector_grp["Avg Score"].round(1)
                    sector_grp = sector_grp.fillna("—")

                    st.dataframe(
                        sector_grp,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Sector":    st.column_config.TextColumn("Sector"),
                            "Symbols":   st.column_config.NumberColumn("Symbols"),
                            "A+":        st.column_config.NumberColumn("A+"),
                            "A":         st.column_config.NumberColumn("A"),
                            "Avg Score": st.column_config.NumberColumn("Avg Score", format="%.1f"),
                        },
                    )
                else:
                    st.info("No 'symbol' column in results DataFrame.")

        except Exception as exc:
            st.warning(f"Sector breakdown unavailable: {exc}")
# If symbol_info.csv doesn't exist — skip silently (no else / no warning shown)
