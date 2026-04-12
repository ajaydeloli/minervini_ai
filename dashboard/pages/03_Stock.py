"""
dashboard/pages/03_Stock.py
────────────────────────────
Single-stock deep-dive page for the Minervini SEPA Dashboard.

Design doc: "Single stock deep-dive (chart + TT checklist + VCP + fundamentals + LLM brief)"

Sections
────────
  1. Symbol selector  (pre-populated from st.query_params["symbol"])
  2. Score header row  (gauge · stage · RS rating · breakout status)
  3. Chart             (cached PNG → live mplfinance fallback)
  4. Detail tabs:
       📐 Setup       — TT checklist + VCP summary + Entry/Stop/R:R
       📊 Fundamentals — 7-condition scorecard + key metrics
       📰 News        — sentiment gauge + top-5 matching articles
       🤖 AI Brief    — LLM narrative + [Generate Now] button
       📈 History     — score timeline + quality tag history + table
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Project root on sys.path  (pages/ → dashboard/ → project root)
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DB_PATH       = str(_PROJECT_ROOT / "data" / "minervini.db")
_FEATURES_DIR  = _PROJECT_ROOT / "data" / "features"
_CHARTS_DIR    = _PROJECT_ROOT / "data" / "charts"
_NEWS_FILE     = _PROJECT_ROOT / "data" / "news" / "market_news.json"


# ─────────────────────────────────────────────────────────────────────────────
# Cached DB helpers
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _load_screened_symbols(db_path: str) -> list[str]:
    """Return all distinct symbols ever screened, newest-first by last run_date."""
    syms: list[str] = []
    for table, col in [("screener_results", "run_date"), ("sepa_results", "date")]:
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            rows = conn.execute(
                f"SELECT DISTINCT symbol FROM {table} ORDER BY symbol ASC"
            ).fetchall()
            conn.close()
            if rows:
                syms = [r[0] for r in rows]
                break
        except Exception:
            pass
    return syms


@st.cache_data(ttl=300)
def _load_available_dates(db_path: str) -> list[str]:
    """Return all distinct screen dates, newest first."""
    for table, col in [("screener_results", "run_date"), ("sepa_results", "date")]:
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            rows = conn.execute(
                f"SELECT DISTINCT {col} FROM {table} ORDER BY {col} DESC"
            ).fetchall()
            conn.close()
            if rows:
                return [r[0] for r in rows]
        except Exception:
            pass
    return []


@st.cache_data(ttl=300)
def _load_sepa_result(db_path: str, symbol: str, date_str: str) -> dict | None:
    """
    Load a single SEPAResult row for (symbol, date) from the DB.
    Tries screener_results first, then sepa_results for compatibility.
    Returns None if no data found.
    """
    # --- screener_results (written by sqlite_store.save_results) ---
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM screener_results WHERE symbol=? AND run_date=?",
            (symbol.upper(), date_str),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass

    # --- sepa_results (written by screener/results.persist_results) ---
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sepa_results WHERE symbol=? AND date=?",
            (symbol.upper(), date_str),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


@st.cache_data(ttl=300)
def _load_symbol_history(db_path: str, symbol: str) -> list[dict]:
    """Return all historical screen rows for *symbol*, newest first."""
    rows: list[dict] = []
    for table, col in [("screener_results", "run_date"), ("sepa_results", "date")]:
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            result = conn.execute(
                f"SELECT * FROM {table} WHERE symbol=? ORDER BY {col} DESC",
                (symbol.upper(),),
            ).fetchall()
            conn.close()
            if result:
                rows = [dict(r) for r in result]
                break
        except Exception:
            pass
    return rows


@st.cache_data(ttl=300)
def _load_feature_parquet(symbol: str, lookback: int = 90) -> pd.DataFrame:
    """Load the last *lookback* rows from the feature Parquet for *symbol*."""
    path = _FEATURES_DIR / f"{symbol}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        return df.iloc[-lookback:].copy() if len(df) >= lookback else df.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def _load_news() -> list[dict]:
    """Load cached market news articles from data/news/market_news.json."""
    if not _NEWS_FILE.exists():
        return []
    try:
        with open(_NEWS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_result_json(row: dict) -> dict:
    """Safely parse the result_json / data blob from a DB row."""
    for key in ("result_json", "data", "details"):
        raw = row.get(key)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return {}


def _make_sepa_namespace(row: dict) -> SimpleNamespace:
    """
    Build a SimpleNamespace that mimics SEPAResult attributes, merging the
    top-level DB columns with any nested detail dicts from result_json.
    Used by generate_trade_brief() which expects attribute access.
    """
    blob = _parse_result_json(row)
    merged = {**blob, **{k: v for k, v in row.items() if k != "result_json"}}

    # Ensure expected nested dicts exist
    merged.setdefault("trend_template_details", blob.get("trend_template_details") or {})
    merged.setdefault("fundamental_details",    blob.get("fundamental_details") or {})
    merged.setdefault("vcp_details",            blob.get("vcp_details") or {})
    merged.setdefault("narrative",              blob.get("narrative"))
    merged.setdefault("vcp_grade",              blob.get("vcp_grade", ""))
    merged.setdefault("rr_ratio",               blob.get("rr_ratio"))
    merged.setdefault("target_price",           blob.get("target_price"))
    merged.setdefault("conditions_met",         row.get("conditions_met", 0))

    # date / symbol coercion
    merged.setdefault("date", row.get("run_date") or row.get("date", ""))
    merged.setdefault("symbol", row.get("symbol", ""))

    return SimpleNamespace(**merged)


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist helpers  (not cached — mutating ops need fresh reads)
# ─────────────────────────────────────────────────────────────────────────────

def _in_watchlist(symbol: str) -> bool:
    try:
        from storage import sqlite_store as ss
        ss.init_db(_DB_PATH)
        return ss.symbol_in_watchlist(symbol)
    except Exception:
        return False


def _add_watchlist(symbol: str) -> bool:
    try:
        from storage import sqlite_store as ss
        ss.init_db(_DB_PATH)
        return ss.add_symbol(symbol, added_via="dashboard")
    except Exception:
        return False


def _remove_watchlist(symbol: str) -> bool:
    try:
        from storage import sqlite_store as ss
        ss.init_db(_DB_PATH)
        return ss.remove_symbol(symbol)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Section helpers
# ─────────────────────────────────────────────────────────────────────────────

def _breakout_label(row: dict) -> tuple[str, str]:
    """Return (icon+label, color) for the breakout status metric."""
    triggered = bool(row.get("breakout_triggered"))
    vcp_ok = bool(row.get("vcp_qualified"))
    if triggered:
        return "🟢 Breakout Triggered", "#22c55e"
    elif vcp_ok:
        return "⏳ Watching", "#f59e0b"
    else:
        return "❌ No Setup", "#ef4444"


def _rr_colour(rr: float | None) -> str:
    if rr is None:
        return "#8b949e"
    if rr >= 2:
        return "#22c55e"
    if rr >= 1:
        return "#f59e0b"
    return "#ef4444"


def _safe_float(val: Any, decimals: int = 2) -> str:
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _news_sentiment_label(score: float | None) -> tuple[str, str]:
    if score is None:
        return "⬜ N/A", "#8b949e"
    if score > 20:
        return "🟢 Positive", "#22c55e"
    if score < -20:
        return "🔴 Negative", "#ef4444"
    return "🟡 Neutral", "#f59e0b"


def _filter_news_for_symbol(articles: list[dict], symbol: str) -> list[dict]:
    """
    Filter news articles that mention the symbol or its common variants.
    Tries aliases from data/news/ or falls back to simple case-insensitive match.
    """
    sym_lower = symbol.lower()
    # Try to load alias map from config
    aliases: list[str] = [sym_lower]
    try:
        import yaml
        alias_path = _PROJECT_ROOT / "config" / "symbol_aliases.yaml"
        if alias_path.exists():
            with open(alias_path) as f:
                alias_map = yaml.safe_load(f) or {}
            extras = alias_map.get(symbol.upper(), [])
            aliases.extend([a.lower() for a in extras])
    except Exception:
        pass

    matched = []
    for art in articles:
        text = " ".join([
            str(art.get("title", "")),
            str(art.get("description", "")),
            str(art.get("content", "")),
        ]).lower()
        if any(alias in text for alias in aliases):
            matched.append(art)
    return matched[:5]


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# PAGE LAYOUT BEGINS HERE
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

# ── Back link ─────────────────────────────────────────────────────────────────
st.page_link("pages/02_Screener.py", label="← Back to Screener", icon="🔍")

st.title("🔬 Stock Deep-Dive")
st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — Symbol selector
# ─────────────────────────────────────────────────────────────────────────────

all_symbols = _load_screened_symbols(_DB_PATH)
available_dates = _load_available_dates(_DB_PATH)

if not all_symbols:
    st.warning("⚠️ No screened symbols found in the database. Run the pipeline first.")
    st.stop()

# Pre-populate from query param (set by Screener page when user clicks a symbol)
_qp_symbol = st.query_params.get("symbol", "")
_default_sym_idx = 0
if _qp_symbol and _qp_symbol.upper() in all_symbols:
    _default_sym_idx = all_symbols.index(_qp_symbol.upper())

_sel_col1, _sel_col2, _sel_col3 = st.columns([2, 2, 1])

with _sel_col1:
    symbol = st.selectbox(
        "Select symbol",
        options=all_symbols,
        index=_default_sym_idx,
        key="stock_symbol_selector",
    )

with _sel_col2:
    if available_dates:
        date_str = st.selectbox(
            "Screen date",
            options=available_dates,
            index=0,
            key="stock_date_selector",
        )
    else:
        date_str = ""
        st.info("No screen dates available.")

with _sel_col3:
    # Watchlist indicator + toggle
    on_wl = _in_watchlist(symbol)
    wl_icon = "★" if on_wl else "☆"
    wl_label = f"{wl_icon}  {'On Watchlist' if on_wl else 'Not Watched'}"
    st.markdown(f"<div style='margin-top:1.8rem; font-size:1.2rem;'>{wl_label}</div>",
                unsafe_allow_html=True)
    if on_wl:
        if st.button("Remove from Watchlist", key="wl_remove"):
            _remove_watchlist(symbol)
            st.cache_data.clear()
            st.rerun()
    else:
        if st.button("Add to Watchlist ★", key="wl_add"):
            _add_watchlist(symbol)
            st.cache_data.clear()
            st.rerun()

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Load the SEPAResult for the selected symbol + date
# ─────────────────────────────────────────────────────────────────────────────

if not date_str:
    st.info("Select a date to load stock data.")
    st.stop()

row = _load_sepa_result(_DB_PATH, symbol, date_str)

if row is None:
    st.error(f"No data found for **{symbol}** on {date_str}. "
             "Run the pipeline for this date or select a different date.")
    st.stop()

blob = _parse_result_json(row)
sepa = _make_sepa_namespace(row)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — Score header row  (4 columns)
# ─────────────────────────────────────────────────────────────────────────────

score   = int(row.get("score") or 0)
quality = row.get("setup_quality") or "FAIL"
stage   = int(row.get("stage") or 0)
stage_lbl = row.get("stage_label") or f"Stage {stage}"
stage_conf = int(row.get("stage_confidence") or 0)
rs_rating  = int(row.get("rs_rating") or 0)

_hcol1, _hcol2, _hcol3, _hcol4 = st.columns(4)

with _hcol1:
    try:
        from dashboard.components.metrics import render_score_gauge
        render_score_gauge(score, quality)
    except ImportError:
        st.metric("SEPA Score", f"{score} / 100")
        st.progress(score / 100)

with _hcol2:
    _stage_colour = {2: "#22c55e", 1: "#8b949e", 3: "#f59e0b", 4: "#ef4444"}.get(stage, "#8b949e")
    st.markdown(
        f"<div style='margin-top:0.4rem;'>"
        f"<div style='font-size:0.78rem;text-transform:uppercase;color:#8b949e;"
        f"letter-spacing:0.05em;'>Stage</div>"
        f"<div style='font-size:1.25rem;font-weight:700;color:{_stage_colour};margin:0.25rem 0;'>"
        f"{stage_lbl}</div>"
        f"<div style='font-size:0.85rem;color:#8b949e;'>Confidence: "
        f"<b style='color:#e6edf3;'>{stage_conf}%</b></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

with _hcol3:
    # RS rating delta vs prior week if history available
    hist = _load_symbol_history(_DB_PATH, symbol)
    _rs_delta: int | None = None
    if len(hist) >= 2:
        prev_rs = hist[1].get("rs_rating")
        if prev_rs is not None:
            _rs_delta = rs_rating - int(prev_rs)
    st.metric(
        "RS Rating",
        value=rs_rating,
        delta=_rs_delta if _rs_delta is not None else None,
        help="Relative Strength Rating vs Nifty 500 (0-99). Higher is stronger.",
    )

with _hcol4:
    _bo_label, _bo_colour = _breakout_label(row)
    st.markdown(
        f"<div style='margin-top:0.4rem;'>"
        f"<div style='font-size:0.78rem;text-transform:uppercase;color:#8b949e;"
        f"letter-spacing:0.05em;'>Breakout Status</div>"
        f"<div style='font-size:1.1rem;font-weight:700;color:{_bo_colour};"
        f"margin-top:0.5rem;'>{_bo_label}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — Chart (full width)
# ─────────────────────────────────────────────────────────────────────────────

lookback = st.slider("Chart lookback (days)", min_value=30, max_value=252, value=90, step=10)

_chart_rendered = False

# 1. Try fast cached PNG
try:
    from dashboard.components.charts import render_cached_chart
    _fig = render_cached_chart(symbol, date_str)
    if _fig is not None:
        st.pyplot(_fig, use_container_width=True)
        _chart_rendered = True
except Exception:
    pass

# 2. Live render from feature Parquet
if not _chart_rendered:
    _feat_df = _load_feature_parquet(symbol, lookback)
    if not _feat_df.empty:
        try:
            from dashboard.components.charts import render_candlestick_chart
            _fig = render_candlestick_chart(symbol, _feat_df, sepa)
            if _fig is not None:
                st.pyplot(_fig, use_container_width=True)
                _chart_rendered = True
        except Exception as _chart_err:
            st.warning(f"Chart render error: {_chart_err}")

if not _chart_rendered:
    st.info(f"No chart available for {symbol} on {date_str}. "
            "Run `pipeline/runner.py` to generate charts, or ensure the "
            f"feature Parquet exists at `data/features/{symbol}.parquet`.")

st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — Detail tabs
# ─────────────────────────────────────────────────────────────────────────────

tab_setup, tab_fund, tab_news, tab_ai, tab_hist = st.tabs(
    ["📐 Setup", "📊 Fundamentals", "📰 News", "🤖 AI Brief", "📈 History"]
)

# ══════════════════════════════════════════════════════════
# TAB 1 — Setup: TT checklist + VCP + Entry/Stop/RR
# ══════════════════════════════════════════════════════════

with tab_setup:
    _t1_left, _t1_right = st.columns(2)

    with _t1_left:
        st.subheader("📋 Trend Template")
        try:
            from dashboard.components.metrics import render_trend_template_checklist
            _tt_details = getattr(sepa, "trend_template_details", {}) or {}
            _conds_met   = int(row.get("conditions_met") or 0)
            render_trend_template_checklist(_tt_details, _conds_met)
        except ImportError:
            _tt_details = getattr(sepa, "trend_template_details", {}) or {}
            if _tt_details:
                for cond, passed in _tt_details.items():
                    icon = "✅" if passed else "❌"
                    st.markdown(f"{icon}  **{cond}**")
            else:
                st.info("Trend template detail not available.")

    with _t1_right:
        st.subheader("🌀 VCP Pattern")
        try:
            from dashboard.components.metrics import render_vcp_summary
            _vcp_details   = getattr(sepa, "vcp_details", {}) or {}
            _vcp_qualified = bool(row.get("vcp_qualified"))
            render_vcp_summary(_vcp_details, _vcp_qualified)
        except ImportError:
            _vcp_details = getattr(sepa, "vcp_details", {}) or {}
            _vcp_ok = bool(row.get("vcp_qualified"))
            st.markdown(f"**VCP Qualified:** {'✅ Yes' if _vcp_ok else '❌ No'}")
            if _vcp_details:
                for k, v in _vcp_details.items():
                    st.markdown(f"- **{k.replace('_', ' ').title()}:** {v}")

    st.divider()

    # ── Entry / Stop / Risk row ───────────────────────────────────────────────
    st.subheader("📍 Trade Levels")
    _tc1, _tc2, _tc3, _tc4 = st.columns(4)

    _entry = row.get("entry_price")
    _stop  = row.get("stop_loss")
    _risk  = row.get("risk_pct")
    _rr    = getattr(sepa, "rr_ratio", None) or blob.get("rr_ratio")
    _target = getattr(sepa, "target_price", None) or blob.get("target_price")

    _tc1.metric("Entry Price", _safe_float(_entry) if _entry else "N/A")
    _tc2.metric("Stop Loss",   _safe_float(_stop)  if _stop  else "N/A")
    _tc3.metric("Risk %",      f"{_safe_float(_risk)}%" if _risk else "N/A")
    _tc4.metric("Target Price",_safe_float(_target) if _target else "N/A")

    # R:R ratio with colour
    _rr_col = _rr_colour(_rr)
    _rr_display = _safe_float(_rr, 2) if _rr is not None else "N/A"
    st.markdown(
        f"**Risk:Reward Ratio** &nbsp; "
        f"<span style='font-size:1.5rem;font-weight:700;color:{_rr_col};'>"
        f"{_rr_display}</span>"
        + (
            f" &nbsp;<span style='color:{_rr_col};font-size:0.9rem;'>"
            f"({'Excellent' if (_rr or 0) >= 2 else 'Acceptable' if (_rr or 0) >= 1 else 'Poor'})</span>"
            if _rr is not None else ""
        ),
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════
# TAB 2 — Fundamentals
# ══════════════════════════════════════════════════════════

with tab_fund:
    _fund_details = getattr(sepa, "fundamental_details", {}) or blob.get("fundamental_details") or {}
    _fund_pass    = bool(row.get("fundamental_pass"))

    st.subheader("📊 Fundamental Scorecard")
    try:
        from dashboard.components.metrics import render_fundamental_scorecard
        render_fundamental_scorecard(_fund_details, _fund_pass)
    except ImportError:
        if _fund_details:
            for cond, passed in _fund_details.items():
                if isinstance(passed, bool):
                    icon = "✅" if passed else "❌"
                    st.markdown(f"{icon} **{cond.replace('_', ' ').title()}**")
        else:
            st.info("Fundamental detail not available.")

    st.divider()
    st.subheader("Key Metrics")
    _fmc1, _fmc2, _fmc3, _fmc4 = st.columns(4)

    def _fund_val(key: str) -> str:
        v = _fund_details.get(key)
        return _safe_float(v) if v is not None else "N/A"

    _fmc1.metric("P/E Ratio",         _fund_val("pe_ratio"))
    _fmc2.metric("ROE (%)",            _fund_val("roe"))
    _fmc3.metric("D/E Ratio",          _fund_val("debt_to_equity"))
    _fmc4.metric("Promoter Holding %", _fund_val("promoter_holding"))

    # Cache age
    _cached_at = _fund_details.get("fetched_at") or _fund_details.get("cached_at", "")
    if _cached_at:
        try:
            from datetime import datetime, timezone
            _fetched_dt = datetime.fromisoformat(_cached_at)
            _age_days   = (datetime.now(timezone.utc) - _fetched_dt).days
            st.caption(f"Data source: Screener.in | Cached {_age_days} day(s) ago")
        except Exception:
            st.caption(f"Data source: Screener.in | Cached {_cached_at}")
    else:
        st.caption("Data source: Screener.in")

    if not _fund_details:
        st.info("No fundamental data available for this symbol. "
                "Enable fundamentals scraping in `config/settings.yaml` "
                "and re-run the pipeline.")

# ══════════════════════════════════════════════════════════
# TAB 3 — News
# ══════════════════════════════════════════════════════════

with tab_news:
    st.subheader("📰 News Sentiment")

    _news_score = row.get("news_score")
    _ns_label, _ns_colour = _news_sentiment_label(_news_score)

    _ns_val_str = f"{_news_score:+.1f}" if _news_score is not None else "N/A"
    st.markdown(
        f"<div style='font-size:1.8rem;font-weight:700;color:{_ns_colour};"
        f"margin-bottom:0.5rem;'>{_ns_label} &nbsp; "
        f"<span style='font-size:1.2rem;'>({_ns_val_str})</span></div>",
        unsafe_allow_html=True,
    )
    st.progress(
        min(1.0, max(0.0, (_news_score + 100) / 200))
        if _news_score is not None else 0.5
    )
    st.caption("Sentiment scale: −100 (very bearish) to +100 (very bullish) | "
               "Positive > 20 | Neutral −20 to 20 | Negative < −20")

    st.divider()
    st.subheader(f"Top Articles mentioning {symbol}")

    _all_news = _load_news()
    if _all_news:
        _sym_news = _filter_news_for_symbol(_all_news, symbol)
        if _sym_news:
            for _art in _sym_news:
                _art_title  = _art.get("title", "Untitled")
                _art_source = _art.get("source", _art.get("feed_name", "Unknown"))
                _art_url    = _art.get("url", _art.get("link", "#"))
                _art_score  = _art.get("sentiment_score", _art.get("score", None))
                _art_sent   = _art.get("sentiment", "")

                _art_ns_label, _art_ns_col = _news_sentiment_label(_art_score)
                _badge = (f"<span style='background:{_art_ns_col};color:#000;"
                          f"padding:1px 8px;border-radius:10px;font-size:0.75rem;"
                          f"font-weight:700;'>{_art_ns_label}</span>")
                st.markdown(
                    f"**[{_art_title}]({_art_url})**  "
                    f"&nbsp; {_badge} &nbsp; "
                    f"<span style='color:#8b949e;font-size:0.82rem;'>— {_art_source}</span>",
                    unsafe_allow_html=True,
                )
                st.write("")
        else:
            st.info(f"No cached articles found mentioning **{symbol}**.")
    else:
        st.info("No news cache found. Run the pipeline to fetch market news "
                "(`data/news/market_news.json` will be populated automatically).")

# ══════════════════════════════════════════════════════════
# TAB 4 — AI Brief
# ══════════════════════════════════════════════════════════

with tab_ai:
    st.subheader("🤖 AI Trade Brief")

    _narrative = getattr(sepa, "narrative", None) or blob.get("narrative")

    if _narrative:
        st.markdown(_narrative)
    else:
        st.info("AI brief not available. Enable LLM in `config/settings.yaml` "
                "(`llm.enabled: true`) and re-run the pipeline to generate it.")

    st.divider()
    if st.button("⚡ Generate Now", key="ai_generate_btn",
                 help="Calls the LLM inline — requires llm.enabled: true in settings.yaml"):
        try:
            import yaml
            _cfg_path = _PROJECT_ROOT / "config" / "settings.yaml"
            with open(_cfg_path, "r") as _f:
                _config = yaml.safe_load(_f)

            if not _config.get("llm", {}).get("enabled", False):
                st.warning("LLM is disabled in `config/settings.yaml`. "
                           "Set `llm.enabled: true` to use this feature.")
            else:
                _feat_df_ai = _load_feature_parquet(symbol, 90)
                with st.spinner(f"Generating AI brief for {symbol}…"):
                    from llm.explainer import generate_trade_brief
                    # Temporarily override quality filter so inline generate always works
                    _cfg_ai = {**_config, "llm": {**_config.get("llm", {}),
                               "only_for_quality": ["A+", "A", "B", "C", "FAIL"]}}
                    _brief = generate_trade_brief(sepa, _feat_df_ai, _cfg_ai)
                if _brief:
                    st.success("Brief generated!")
                    st.markdown(_brief)
                else:
                    st.warning("LLM returned no output. Check your API key and provider config.")
        except FileNotFoundError:
            st.error("Config file not found at `config/settings.yaml`.")
        except ImportError as _ie:
            st.error(f"Could not import LLM module: {_ie}")
        except Exception as _e:
            st.error(f"Generation failed: {_e}")

# ══════════════════════════════════════════════════════════
# TAB 5 — History
# ══════════════════════════════════════════════════════════

with tab_hist:
    st.subheader(f"📈 Score History — {symbol}")

    _hist_rows = _load_symbol_history(_DB_PATH, symbol)

    if not _hist_rows:
        st.info(f"No historical screen data found for **{symbol}**.")
    else:
        _hist_df = pd.DataFrame(_hist_rows)

        # Normalise date column
        for _dc in ("run_date", "date", "screen_date"):
            if _dc in _hist_df.columns:
                _hist_df = _hist_df.rename(columns={_dc: "date"})
                break

        if "date" in _hist_df.columns and "score" in _hist_df.columns:
            _chart_df = (
                _hist_df[["date", "score"]]
                .dropna(subset=["score"])
                .sort_values("date")
                .set_index("date")
            )
            st.line_chart(_chart_df["score"], height=220,
                          use_container_width=True)
        else:
            st.info("Score or date column not found in history data.")

        # Quality tag timeline
        if "setup_quality" in _hist_df.columns and "date" in _hist_df.columns:
            st.subheader("Quality Tag Timeline")
            _qt_df = _hist_df[["date", "setup_quality"]].dropna()
            _qt_str = " → ".join(
                f"**{r['setup_quality']}** ({r['date']})"
                for _, r in _qt_df.sort_values("date").iterrows()
            )
            st.markdown(_qt_str if _qt_str else "No quality tag history.")

        st.divider()
        st.subheader("All Historical Results")

        _display_cols = [c for c in [
            "date", "score", "setup_quality", "stage", "rs_rating",
            "entry_price", "stop_loss", "risk_pct",
            "trend_template_pass", "vcp_qualified", "breakout_triggered",
        ] if c in _hist_df.columns]

        _show_df = _hist_df[_display_cols].sort_values("date", ascending=False)
        # Boolean columns → readable labels
        for _bc in ("trend_template_pass", "vcp_qualified", "breakout_triggered"):
            if _bc in _show_df.columns:
                _show_df[_bc] = _show_df[_bc].apply(
                    lambda v: "✅" if bool(v) else "❌"
                )
        st.dataframe(_show_df, use_container_width=True, hide_index=True)