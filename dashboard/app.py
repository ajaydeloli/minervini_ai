"""
dashboard/app.py
────────────────
Streamlit multi-page app entry point for the Minervini SEPA dashboard.

Defines:
  - Global page config + dark-theme CSS injection
  - Sidebar: market status, last run info, quick stats
  - Home page: KPI summary, A+ preview table
  - Shared helper functions imported by all child pages

Run with:
    streamlit run dashboard/app.py --server.port 8501
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────────────────────
# Project root on sys.path so `from storage.sqlite_store import ...` works
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Streamlit import (must come after sys.path patch)
# ─────────────────────────────────────────────────────────────────────────────

import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Page configuration  — MUST be the first Streamlit call
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Minervini SEPA Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Custom CSS injection — dark theme + component styling
# ─────────────────────────────────────────────────────────────────────────────

_CSS = """
<style>
/* ── Google Fonts: JetBrains Mono for prices ──────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap');

/* ── Force dark theme CSS variables ─────────────────────────────────────── */
:root {
    --bg-primary:      #0e1117;
    --bg-secondary:    #161b22;
    --bg-card:         #1c2230;
    --border-color:    #30363d;
    --text-primary:    #e6edf3;
    --text-secondary:  #8b949e;
    --accent-green:    #22c55e;
    --accent-gold:     #FFD700;
    --accent-amber:    #f59e0b;
    --accent-gray:     #6b7280;
    --accent-blue:     #3b82f6;
}

/* ── App background ──────────────────────────────────────────────────────── */
.stApp {
    background-color: var(--bg-primary);
    color: var(--text-primary);
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background-color: var(--bg-secondary) !important;
    border-right: 1px solid var(--border-color) !important;
}
section[data-testid="stSidebar"] * {
    color: var(--text-primary) !important;
}

/* ── Metric cards ────────────────────────────────────────────────────────── */
div[data-testid="metric-container"] {
    background-color: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 1rem 1.2rem;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
}
div[data-testid="metric-container"] label {
    color: var(--text-secondary) !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
div[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 1.6rem !important;
    font-weight: 600;
    color: var(--text-primary) !important;
}

/* ── Price / numeric values monospace ───────────────────────────────────── */
.mono { font-family: 'JetBrains Mono', monospace; }

/* ── Quality badges ──────────────────────────────────────────────────────── */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.04em;
}
.badge-aplus { background-color: #FFD700; color: #1a1a00; }
.badge-a     { background-color: #22c55e; color: #001a00; }
.badge-b     { background-color: #f59e0b; color: #1a0d00; }
.badge-c     { background-color: #6b7280; color: #ffffff; }
.badge-fail  { background-color: #ef4444; color: #ffffff; }

/* ── Divider ─────────────────────────────────────────────────────────────── */
hr { border-color: var(--border-color) !important; }

/* ── Dataframe / table ───────────────────────────────────────────────────── */
div[data-testid="stDataFrame"] th {
    background-color: var(--bg-secondary) !important;
    color: var(--text-secondary) !important;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

/* ── Info / warning / error boxes ───────────────────────────────────────── */
div[data-testid="stAlert"] {
    border-radius: 8px;
}
</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper functions  (importable by child pages)
# ─────────────────────────────────────────────────────────────────────────────

def load_db_path() -> str:
    """Return path to data/minervini.db resolved relative to project root."""
    return str(_PROJECT_ROOT / "data" / "minervini.db")


def get_watchlist_symbols(db_path: str) -> set[str]:
    """
    Load all symbols from SQLite watchlist table.
    Returns an empty set on error.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT symbol FROM watchlist").fetchall()
        conn.close()
        return {row["symbol"] for row in rows}
    except Exception:
        return set()


def get_latest_screen_date(db_path: str) -> str | None:
    """
    Return the most recent run_date in screener_results table, or None
    if the table is empty or does not yet exist.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute(
            "SELECT MAX(run_date) AS d FROM screener_results"
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def get_todays_results(db_path: str, date_str: str | None = None) -> list[dict]:
    """
    Return screener_results rows for the given date (or latest date).

    Rows are sorted: watchlist symbols first, then by score descending.
    Returns an empty list when there are no results or on error.
    """
    try:
        if date_str is None:
            date_str = get_latest_screen_date(db_path)
        if date_str is None:
            return []
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT sr.*, COALESCE(wl.symbol IS NOT NULL, 0) AS on_watchlist
            FROM screener_results sr
            LEFT JOIN watchlist wl ON wl.symbol = sr.symbol
            WHERE sr.run_date = ?
            ORDER BY on_watchlist DESC, sr.score DESC
            """,
            (date_str,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def is_market_open() -> bool:
    """
    Return True if the current IST time falls within a weekday market
    session: Monday–Friday 09:15–15:30 IST.
    """
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz=IST)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    market_open  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers  (sidebar / KPI data)
# ─────────────────────────────────────────────────────────────────────────────

def _get_last_run_info(db_path: str) -> dict:
    """
    Read the most recent successful pipeline run from run_history.
    Returns a dict with keys: run_date, finished_at, duration_sec.
    Returns an empty dict when no successful run exists.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT run_date, finished_at, duration_sec
            FROM run_history
            WHERE status = 'success'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def _count_quality_today(db_path: str, date_str: str | None, quality: str) -> int:
    """Count screener_results rows matching a setup_quality on a given date."""
    if not date_str:
        return 0
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute(
            "SELECT COUNT(*) FROM screener_results WHERE run_date=? AND setup_quality=?",
            (date_str, quality),
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def _count_watchlist(db_path: str) -> int:
    """Return the total number of symbols in the watchlist."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

_DB_PATH = load_db_path()
_latest_date = get_latest_screen_date(_DB_PATH)

with st.sidebar:
    # ── Title ────────────────────────────────────────────────────────────────
    st.markdown(
        """
        <div style='text-align:center; padding: 0.5rem 0 0.25rem 0;'>
            <span style='font-size:2rem;'>📈</span><br>
            <span style='font-size:1.15rem; font-weight:700;
                         letter-spacing:0.03em;'>SEPA System</span><br>
            <span style='font-size:0.72rem; color:#8b949e;
                         text-transform:uppercase; letter-spacing:0.08em;'>
                Minervini · NSE / India
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Market status ─────────────────────────────────────────────────────────
    _open = is_market_open()
    _status_icon  = "🟢" if _open else "🔴"
    _status_label = "Market Open" if _open else "Market Closed"
    st.markdown(
        f"<div style='font-size:0.9rem; padding:0.25rem 0;'>"
        f"{_status_icon} &nbsp;<b>{_status_label}</b></div>",
        unsafe_allow_html=True,
    )

    # ── Last run ──────────────────────────────────────────────────────────────
    st.markdown("**Last run**")
    try:
        _run = _get_last_run_info(_DB_PATH)
        if _run:
            _finished = _run.get("finished_at", "—")
            _dur = _run.get("duration_sec")
            _dur_str = f"  ({_dur:.0f}s)" if _dur else ""
            st.caption(f"📅 {_run.get('run_date', '—')}  ·  {_finished}{_dur_str}")
        else:
            st.caption("No successful run yet")
    except Exception as exc:
        st.error(f"Run history unavailable: {exc}")

    st.divider()

    # ── Quick stats ───────────────────────────────────────────────────────────
    st.markdown("**Today's quick stats**")
    try:
        _aplus = _count_quality_today(_DB_PATH, _latest_date, "A+")
        _a     = _count_quality_today(_DB_PATH, _latest_date, "A")
        _wl    = _count_watchlist(_DB_PATH)
        col1, col2, col3 = st.columns(3)
        col1.metric("A+ setups", _aplus)
        col2.metric("A setups",  _a)
        col3.metric("Watchlist", _wl)
    except Exception as exc:
        st.error(f"Stats unavailable: {exc}")

    st.divider()
    st.caption("Use the **Pages** menu above to navigate.")
    st.markdown(
        "<div style='text-align:center; font-size:0.68rem; "
        "color:#8b949e; padding-top:0.5rem;'>"
        "Minervini SEPA v1.5.0 | NSE/India</div>",
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Main body — home page
# ─────────────────────────────────────────────────────────────────────────────

# ── Header ────────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")
_now_ist = datetime.now(tz=IST)
_today_str = _now_ist.strftime("%A, %d %B %Y")
_market_tag = "🟢 Market Open" if is_market_open() else "🔴 Market Closed"

st.title("📈 Minervini SEPA System")
st.markdown(
    f"<p style='color:#8b949e; margin-top:-0.5rem;'>"
    f"{_today_str} &nbsp;·&nbsp; {_market_tag}</p>",
    unsafe_allow_html=True,
)
st.divider()

# ── Run summary KPIs ──────────────────────────────────────────────────────────
try:
    from dashboard.components.metrics import render_run_summary_kpis
    render_run_summary_kpis(db_path=_DB_PATH)
except ImportError:
    # components not yet built — graceful fallback
    st.info("KPI component not available yet (dashboard/components/metrics.py).")
except Exception as exc:
    st.error(f"Could not load run KPIs: {exc}")

st.divider()

# ── A+ preview table ──────────────────────────────────────────────────────────
st.subheader("🏆 Today's Best Setups (A+)")

if not _latest_date:
    st.info("No screen results yet for today. Run the pipeline.")
else:
    try:
        _all_results = get_todays_results(_DB_PATH, _latest_date)
        _aplus_rows  = [r for r in _all_results if r.get("setup_quality") == "A+"][:5]

        if not _aplus_rows:
            st.info("No A+ setups found for today. Run the pipeline or check back after market hours.")
        else:
            try:
                from dashboard.components.tables import render_sepa_results_table
                render_sepa_results_table(
                    results=_aplus_rows,
                    watchlist_symbols=get_watchlist_symbols(_DB_PATH),
                    compact=True,
                )
            except ImportError:
                # tables component not yet built — plain dataframe fallback
                import pandas as pd
                _display_cols = [
                    "symbol", "setup_quality", "score",
                    "entry_price", "stop_loss", "risk_pct",
                    "rs_rating", "stage",
                ]
                _df = pd.DataFrame(_aplus_rows)
                _visible = [c for c in _display_cols if c in _df.columns]
                st.dataframe(_df[_visible], use_container_width=True)
            except Exception as exc:
                st.error(f"Could not render results table: {exc}")

    except Exception as exc:
        st.error(f"Failed to load today's results: {exc}")

    # ── "View full screener" link ─────────────────────────────────────────────
    st.markdown(
        "<div style='text-align:right; margin-top:0.5rem;'>"
        "<a href='/02_Screener' target='_self' "
        "style='color:#3b82f6; font-size:0.9rem;'>"
        "View full screener →</a></div>",
        unsafe_allow_html=True,
    )
