"""
dashboard/pages/05_Backtest.py
───────────────────────────────
Backtest results viewer + regime breakdown table.

Shows historical backtest runs loaded from data/backtests/, allows
triggering new backtest runs via subprocess, and presents:
  - Summary KPIs (CAGR, Sharpe, MaxDD, Win Rate, Trade Count)
  - Equity curve with regime shading
  - Regime breakdown table
  - All-trades table with filters + CSV export
  - Quality breakdown bar chart
  - Parameter-sweep heatmap (if available)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# ── Project root on sys.path (same pattern as app.py) ────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Paths ─────────────────────────────────────────────────────────────────────
_BACKTEST_DIR = _PROJECT_ROOT / "data" / "backtests"
_BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"

_IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
# Filesystem helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scan_backtest_runs() -> list[Path]:
    """
    Scan data/backtests/ for backtest trade CSV files (backtest_*.csv),
    returning them sorted newest-first.
    """
    files = sorted(_BACKTEST_DIR.glob("backtest_*.csv"), reverse=True)
    return files


def _run_label(csv_path: Path) -> str:
    """Human-friendly label derived from the filename."""
    stem = csv_path.stem                      # e.g. "backtest_nifty500_2022-01-01_2024-01-01_2026-04-12"
    parts = stem.replace("backtest_", "", 1)  # drop prefix
    return parts


def _metrics_path(csv_path: Path) -> Path:
    """Companion metrics JSON for a given trades CSV."""
    return csv_path.with_name(csv_path.stem.replace("backtest_", "metrics_") + ".json")


def _sweep_path(csv_path: Path) -> Path:
    """Companion parameter sweep CSV (if it was produced)."""
    return csv_path.with_name(csv_path.stem.replace("backtest_", "sweep_") + ".csv")


def _chart_path(csv_path: Path) -> Path:
    """Companion equity-curve PNG."""
    return csv_path.with_name(csv_path.stem.replace("backtest_", "equity_curve_") + ".png")


# ─────────────────────────────────────────────────────────────────────────────
# Cached data loaders
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def _load_trades(csv_path: str) -> pd.DataFrame:
    """Load trades CSV; coerce types; return empty DataFrame on error."""
    try:
        df = pd.read_csv(csv_path)
        for col in ("entry_date", "exit_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        for col in ("pnl", "pnl_pct", "r_multiple", "entry_price",
                    "exit_price", "initial_risk", "qty"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        return df
    except Exception as exc:
        st.warning(f"Could not load trades: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def _load_metrics_json(metrics_path: str) -> dict:
    """Load pre-computed metrics JSON if it exists; return {} otherwise."""
    p = Path(metrics_path)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


@st.cache_data(ttl=3600)
def _load_sweep_csv(sweep_path: str) -> pd.DataFrame | None:
    """Load parameter-sweep CSV if it exists; return None otherwise."""
    p = Path(sweep_path)
    if p.exists():
        try:
            return pd.read_csv(p)
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Metrics computation (live, from trades DataFrame)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def _compute_metrics(csv_path: str, initial_capital: float = 100_000.0) -> dict:
    """
    Try to load pre-saved metrics JSON; fall back to computing from trades CSV.
    Returns a flat dict with keys: cagr, sharpe_ratio, max_drawdown_pct,
    win_rate, total_trades.
    """
    mpath = str(_metrics_path(Path(csv_path)))
    saved = _load_metrics_json(mpath)
    if saved:
        return saved

    trades_df = _load_trades(csv_path)
    if trades_df.empty:
        return {"cagr": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0,
                "win_rate": 0.0, "total_trades": 0}
    try:
        from backtest.metrics import compute_metrics
        trades_list = trades_df.to_dict("records")
        m = compute_metrics(trades_list, initial_capital)
        return {
            "cagr": m.cagr, "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown_pct": m.max_drawdown_pct,
            "win_rate": m.win_rate, "total_trades": m.total_trades,
        }
    except Exception:
        total = len(trades_df)
        wins  = int((trades_df["pnl"] > 0).sum()) if "pnl" in trades_df.columns else 0
        return {"cagr": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0,
                "win_rate": wins / total * 100 if total else 0.0,
                "total_trades": total}


@st.cache_data(ttl=3600)
def _compute_equity_curve(csv_path: str, initial_capital: float = 100_000.0) -> pd.DataFrame:
    """Build equity curve DataFrame from trades CSV."""
    trades_df = _load_trades(csv_path)
    if trades_df.empty:
        return pd.DataFrame(columns=["equity", "daily_return_pct"])
    try:
        from backtest.metrics import compute_equity_curve
        trades_list = trades_df.to_dict("records")
        return compute_equity_curve(trades_list, initial_capital)
    except Exception:
        return pd.DataFrame(columns=["equity", "daily_return_pct"])


@st.cache_data(ttl=3600)
def _compute_regime_breakdown(csv_path: str) -> dict:
    """Compute per-regime stats from trades CSV."""
    trades_df = _load_trades(csv_path)
    if trades_df.empty or "regime" not in trades_df.columns:
        return {}
    try:
        from backtest.regime import compute_regime_breakdown
        return compute_regime_breakdown(trades_df.to_dict("records"))
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Trailing-stop colour helper
# ─────────────────────────────────────────────────────────────────────────────

def _ts_colour(val: float) -> str:
    """Return colour emoji for trailing-stop percentage."""
    if abs(val - 0.07) < 0.005:
        return "🟢"
    return "🟡" if val > 0.07 else "🔴"


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess / run-state helpers
# ─────────────────────────────────────────────────────────────────────────────

def _init_session_state() -> None:
    defaults = {
        "bt_running":    False,
        "bt_process":    None,   # Popen object (lives only within this session)
        "bt_start_time": None,
        "bt_run_label":  "",
        "bt_last_rc":    None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _poll_running_process() -> None:
    """
    Check whether a background backtest process has finished.
    Updates session_state flags and clears cache on success.
    """
    proc = st.session_state.get("bt_process")
    if proc is None:
        st.session_state.bt_running = False
        return
    rc = proc.poll()
    if rc is not None:                       # process has finished
        st.session_state.bt_running  = False
        st.session_state.bt_last_rc  = rc
        st.session_state.bt_process  = None
        st.cache_data.clear()               # force reload of fresh results


# ─────────────────────────────────────────────────────────────────────────────
# Page bootstrap
# ─────────────────────────────────────────────────────────────────────────────

_init_session_state()
_poll_running_process()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Page header + run selector
# ═════════════════════════════════════════════════════════════════════════════

st.title("🧪 Backtesting")
st.markdown(
    "<p style='color:#8b949e; margin-top:-0.5rem;'>"
    "Walk-forward backtest engine · Minervini SEPA · NSE/India</p>",
    unsafe_allow_html=True,
)

# ── Status banner (if backtest is running) ────────────────────────────────────
if st.session_state.bt_running:
    elapsed = time.time() - (st.session_state.bt_start_time or time.time())
    st.info(
        f"⏳ Backtest running… ({elapsed:.0f}s elapsed)  "
        f"— label: **{st.session_state.bt_run_label}**  \n"
        "Results will appear automatically when complete. "
        "You may leave this page and return.",
        icon="⏳",
    )
    # Auto-rerun every 5 s while the process is alive
    time.sleep(5)
    st.rerun()

# ── Last run result banner ────────────────────────────────────────────────────
if st.session_state.bt_last_rc is not None:
    if st.session_state.bt_last_rc == 0:
        st.success("✅ Last backtest completed successfully.", icon="✅")
    else:
        st.error(
            f"❌ Last backtest exited with code {st.session_state.bt_last_rc}. "
            "Check terminal / logs for details.",
            icon="❌",
        )
    st.session_state.bt_last_rc = None   # show only once

st.divider()

# ── Run selector ──────────────────────────────────────────────────────────────
_available_runs = _scan_backtest_runs()

if not _available_runs:
    st.info(
        "**No backtest results found.**  \n\n"
        "Expand **▶ Run New Backtest** below to run your first backtest.  \n"
        "Results are saved to `data/backtests/` and auto-loaded here.",
        icon="ℹ️",
    )
    _selected_csv: Path | None = None
else:
    _run_labels = {_run_label(p): p for p in _available_runs}
    _chosen_label = st.selectbox(
        "Select backtest run",
        options=list(_run_labels.keys()),
        help="Newest runs appear first. Each entry corresponds to one trades CSV file.",
    )
    _selected_csv = _run_labels[_chosen_label]


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Run New Backtest
# ═════════════════════════════════════════════════════════════════════════════

with st.expander("▶ Run New Backtest", expanded=False):
    st.markdown(
        "⚠️ **Backtests may take 5–20 minutes** depending on universe size and date range.  \n"
        "The page will auto-refresh every 5 seconds until completion."
    )

    with st.form("bt_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            _start_date = st.date_input(
                "Start date",
                value=date(2019, 1, 1),
                min_value=date(2014, 1, 1),
                max_value=date.today(),
            )
        with c2:
            _end_date = st.date_input(
                "End date",
                value=date(2024, 1, 1),
                min_value=date(2014, 1, 1),
                max_value=date.today(),
            )

        _universe = st.selectbox(
            "Universe",
            options=["nifty500", "nifty200", "watchlist"],
            index=0,
            help="Stock universe to backtest against.",
        )

        _ts_raw = st.slider(
            "Trailing stop",
            min_value=0.00, max_value=0.20,
            value=0.07, step=0.01,
            format="%.0f%%",
            help="Trailing stop that follows peak price upward. "
                 "Floored at VCP base_low.",
        )
        _ts_display = f"{_ts_raw * 100:.0f}%"
        _ts_icon = _ts_colour(_ts_raw)
        st.markdown(
            f"Trailing stop: {_ts_icon} **{_ts_display}** "
            f"{'(default)' if abs(_ts_raw - 0.07) < 0.005 else ''}",
            unsafe_allow_html=False,
        )

        _fs_raw = st.slider(
            "Fixed stop fallback",
            min_value=0.00, max_value=0.15,
            value=0.05, step=0.01,
            format="%.0f%%",
            help="Fixed hard stop used when trailing stop is disabled.",
        )

        _max_hold = st.number_input(
            "Max hold days",
            min_value=1, max_value=60, value=20, step=1,
        )

        _submitted = st.form_submit_button(
            "🚀 Run Backtest",
            disabled=st.session_state.bt_running,
        )

    # ── Launch subprocess on submit ───────────────────────────────────────────
    if _submitted:
        if st.session_state.bt_running:
            st.warning("A backtest is already running. Please wait.")
        elif _start_date >= _end_date:
            st.error("Start date must be before end date.")
        else:
            _label = (
                f"{_universe}_{_start_date.isoformat()}"
                f"_{_end_date.isoformat()}"
                f"_{datetime.now(_IST).strftime('%Y%m%d_%H%M%S')}"
            )
            _cmd = [
                sys.executable,
                str(_SCRIPTS_DIR / "backtest_runner.py"),
                "--start",       str(_start_date),
                "--end",         str(_end_date),
                "--universe",    _universe,
                "--trailing-stop", str(_ts_raw),
                "--output-dir",  str(_BACKTEST_DIR),
                "--label",       _label,
            ]
            with st.spinner("Starting backtest…"):
                proc = subprocess.Popen(
                    _cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(_PROJECT_ROOT),
                )
            st.session_state.bt_process    = proc
            st.session_state.bt_running    = True
            st.session_state.bt_start_time = time.time()
            st.session_state.bt_run_label  = _label
            st.session_state.bt_last_rc    = None
            st.rerun()


# ── Guard: nothing to show if no run is selected ─────────────────────────────
if _selected_csv is None:
    st.caption(
        "Past performance does not guarantee future results. "
        "Paper trade for 4–8 weeks before considering live execution."
    )
    st.stop()

# ── Load data for selected run ────────────────────────────────────────────────
_csv_str    = str(_selected_csv)
_trades_df  = _load_trades(_csv_str)
_metrics    = _compute_metrics(_csv_str)
_eq_curve   = _compute_equity_curve(_csv_str)
_regime_bkd = _compute_regime_breakdown(_csv_str)

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Summary KPIs
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("📊 Summary Metrics")

_k1, _k2, _k3, _k4, _k5 = st.columns(5)

_cagr      = _metrics.get("cagr", 0.0)
_sharpe    = _metrics.get("sharpe_ratio", 0.0)
_maxdd     = _metrics.get("max_drawdown_pct", 0.0)
_wr        = _metrics.get("win_rate", 0.0)
_n_trades  = _metrics.get("total_trades", 0)

_k1.metric(
    "CAGR",
    f"{_cagr:.1f}%",
    delta=None,
    help="Compound Annual Growth Rate over the backtest period.",
)
_k2.metric(
    "Sharpe Ratio",
    f"{_sharpe:.2f}",
    help="Annualised Sharpe (daily returns, risk-free = 0).",
)
_k3.metric(
    "Max Drawdown",
    f"{_maxdd:.1f}%",
    help="Largest peak-to-trough decline in equity (always ≤ 0).",
)
_k4.metric(
    "Win Rate",
    f"{_wr:.1f}%",
    help="Percentage of closed trades that were profitable.",
)
_k5.metric(
    "Total Trades",
    str(_n_trades),
    help="Total number of closed trades in this backtest run.",
)

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Equity curve
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("📈 Equity Curve")

if _eq_curve.empty:
    st.info("No equity curve data available for this run.")
else:
    try:
        from dashboard.components.charts import render_backtest_equity_curve
        _fig = render_backtest_equity_curve(_eq_curve)
        if _fig is not None:
            st.pyplot(_fig, use_container_width=True)
        else:
            # Fallback: plain Streamlit line chart
            _eq_display = _eq_curve[["equity"]].copy()
            _eq_display.index = _eq_display.index.strftime("%Y-%m-%d")
            st.line_chart(_eq_display, use_container_width=True)
    except (ImportError, AttributeError):
        # charts.py not yet available — plain fallback
        _eq_display = _eq_curve[["equity"]].copy()
        try:
            _eq_display.index = _eq_display.index.strftime("%Y-%m-%d")
        except Exception:
            pass
        st.line_chart(_eq_display, use_container_width=True)
    except Exception as exc:
        st.warning(f"Could not render equity chart: {exc}")
        _eq_display = _eq_curve[["equity"]].copy()
        st.line_chart(_eq_display, use_container_width=True)

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Regime breakdown
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("🌍 Regime Breakdown")

if not _regime_bkd:
    st.info(
        "No regime data found. Re-run the backtest with the benchmark Parquet "
        "file present at `data/features/NIFTY500.parquet` to enable regime labelling."
    )
else:
    try:
        from dashboard.components.tables import render_backtest_summary_table
        render_backtest_summary_table(_regime_bkd)
    except (ImportError, AttributeError):
        # Fallback: manual regime table
        _regime_rows = []
        for _reg, _stats in _regime_bkd.items():
            _nt = _stats.get("trades", 0)
            _nw = _stats.get("wins", 0)
            _regime_rows.append({
                "Regime":      _reg,
                "Trades":      _nt,
                "Wins":        _nw,
                "Win Rate %":  round(_stats.get("win_rate", 0.0), 1),
                "Avg PnL %":   round(_stats.get("avg_pnl_pct", 0.0), 2),
                "Total PnL ₹": round(_stats.get("total_pnl", 0.0), 0),
            })
        if _regime_rows:
            _reg_df = pd.DataFrame(_regime_rows).set_index("Regime")
            st.dataframe(_reg_df, use_container_width=True)
    except Exception as exc:
        st.warning(f"Could not render regime table: {exc}")

st.caption(
    "📌 Bull markets show the highest win rates for Minervini SEPA.  \n"
    "In **Sideways / Bear** markets, consider reducing position size or "
    "staying in cash until the market recovers Stage 2 conditions."
)
st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Trade analysis tabs
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("🔬 Trade Analysis")

_tab_all, _tab_quality, _tab_sweep = st.tabs([
    "📋 All Trades",
    "📊 Quality Breakdown",
    "⚙️ Parameter Sweep",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — All Trades
# ─────────────────────────────────────────────────────────────────────────────

with _tab_all:
    if _trades_df.empty:
        st.info("No trade data available for this run.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        with st.expander("🔽 Filters", expanded=True):
            _fc1, _fc2, _fc3, _fc4 = st.columns(4)

            # Date range filter
            _min_exit = (
                _trades_df["exit_date"].min().date()
                if "exit_date" in _trades_df.columns and not _trades_df["exit_date"].isna().all()
                else date(2014, 1, 1)
            )
            _max_exit = (
                _trades_df["exit_date"].max().date()
                if "exit_date" in _trades_df.columns and not _trades_df["exit_date"].isna().all()
                else date.today()
            )

            with _fc1:
                _filter_start = st.date_input(
                    "From date", value=_min_exit,
                    min_value=_min_exit, max_value=_max_exit,
                    key="filter_start",
                )
            with _fc2:
                _filter_end = st.date_input(
                    "To date", value=_max_exit,
                    min_value=_min_exit, max_value=_max_exit,
                    key="filter_end",
                )

            # Quality filter
            _quality_opts = ["All"]
            if "setup_quality" in _trades_df.columns:
                _quality_opts += sorted(_trades_df["setup_quality"].dropna().unique().tolist())
            with _fc3:
                _filter_quality = st.selectbox(
                    "Quality", options=_quality_opts, key="filter_quality"
                )

            # Regime filter
            _regime_opts = ["All"]
            if "regime" in _trades_df.columns:
                _regime_opts += sorted(_trades_df["regime"].dropna().unique().tolist())
            with _fc4:
                _filter_regime = st.selectbox(
                    "Regime", options=_regime_opts, key="filter_regime"
                )

            # Stop type filter
            _stop_opts = ["All"]
            if "exit_reason" in _trades_df.columns:
                _possible_stops = _trades_df["exit_reason"].dropna().unique().tolist()
                _trailing = [r for r in _possible_stops if "trailing" in str(r).lower()]
                _fixed    = [r for r in _possible_stops if "fixed" in str(r).lower()
                             or "stop" in str(r).lower()]
                if _trailing:
                    _stop_opts.append("Trailing stop")
                if _fixed:
                    _stop_opts.append("Fixed stop")
            _filter_stop = st.selectbox(
                "Stop type", options=_stop_opts, key="filter_stop"
            )


        # ── Apply filters ─────────────────────────────────────────────────────
        _filtered = _trades_df.copy()

        if "exit_date" in _filtered.columns:
            _filtered = _filtered[
                (_filtered["exit_date"].dt.date >= _filter_start) &
                (_filtered["exit_date"].dt.date <= _filter_end)
            ]

        if _filter_quality != "All" and "setup_quality" in _filtered.columns:
            _filtered = _filtered[_filtered["setup_quality"] == _filter_quality]

        if _filter_regime != "All" and "regime" in _filtered.columns:
            _filtered = _filtered[_filtered["regime"] == _filter_regime]

        if _filter_stop != "All" and "exit_reason" in _filtered.columns:
            if _filter_stop == "Trailing stop":
                _filtered = _filtered[
                    _filtered["exit_reason"].str.contains("trailing", case=False, na=False)
                ]
            elif _filter_stop == "Fixed stop":
                _filtered = _filtered[
                    _filtered["exit_reason"].str.contains(
                        "fixed|stop_loss|stop", case=False, na=False
                    )
                ]

        st.markdown(
            f"Showing **{len(_filtered):,}** of **{len(_trades_df):,}** trades."
        )

        # ── Render table ──────────────────────────────────────────────────────
        try:
            from dashboard.components.tables import render_trades_history_table
            render_trades_history_table(_filtered)
        except (ImportError, AttributeError):
            # Graceful fallback: display key columns in a styled DataFrame
            _display_cols = [
                c for c in [
                    "symbol", "entry_date", "exit_date", "entry_price",
                    "exit_price", "pnl", "pnl_pct", "r_multiple",
                    "exit_reason", "setup_quality", "regime",
                ]
                if c in _filtered.columns
            ]
            _display_df = _filtered[_display_cols].copy()
            for _dc in ("entry_date", "exit_date"):
                if _dc in _display_df.columns:
                    _display_df[_dc] = _display_df[_dc].dt.strftime("%Y-%m-%d")
            for _nc in ("pnl", "pnl_pct", "r_multiple", "entry_price", "exit_price"):
                if _nc in _display_df.columns:
                    _display_df[_nc] = _display_df[_nc].round(2)

            def _colour_pnl(v):
                colour = "#22c55e" if v > 0 else ("#ef4444" if v < 0 else "")
                return f"color: {colour}" if colour else ""

            if "pnl" in _display_df.columns:
                _styled = _display_df.style.applymap(_colour_pnl, subset=["pnl"])
            else:
                _styled = _display_df.style

            st.dataframe(_styled, use_container_width=True, height=420)
        except Exception as exc:
            st.warning(f"Could not render trades table: {exc}")
            st.dataframe(_filtered, use_container_width=True)

        # ── Export CSV ────────────────────────────────────────────────────────
        _export_csv = _filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Export CSV",
            data=_export_csv,
            file_name=f"trades_{_selected_csv.stem}.csv",
            mime="text/csv",
            help="Download filtered trades as CSV.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Quality Breakdown
# ─────────────────────────────────────────────────────────────────────────────

with _tab_quality:
    if _trades_df.empty or "setup_quality" not in _trades_df.columns:
        st.info("No setup-quality data available for this run.")
    else:
        # ── Build per-quality stats ───────────────────────────────────────────
        _QUALITY_ORDER = ["A+", "A", "B", "C", "FAIL"]
        _quality_stats: list[dict] = []

        for _q in _QUALITY_ORDER:
            _qdf = _trades_df[_trades_df["setup_quality"] == _q]
            if _qdf.empty:
                continue
            _qt  = len(_qdf)
            _qw  = int((_qdf["pnl"] > 0).sum()) if "pnl" in _qdf.columns else 0
            _qwr = _qw / _qt * 100 if _qt else 0.0
            _qpl = float(_qdf["pnl_pct"].mean()) if "pnl_pct" in _qdf.columns else 0.0
            _qhd = (
                float(
                    (_qdf["exit_date"] - _qdf["entry_date"])
                    .dt.days.mean()
                )
                if "exit_date" in _qdf.columns and "entry_date" in _qdf.columns
                else 0.0
            )
            _quality_stats.append({
                "Quality":       _q,
                "Trades":        _qt,
                "Wins":          _qw,
                "Win Rate %":    round(_qwr, 1),
                "Avg PnL %":     round(_qpl, 2),
                "Avg Hold Days": round(_qhd, 1),
            })

        if not _quality_stats:
            st.info("No quality breakdown data found.")
        else:
            _qdf_stats = pd.DataFrame(_quality_stats)

            # ── Win-rate bar chart ────────────────────────────────────────────
            st.markdown("#### Win Rate by Setup Quality")
            _chart_data = _qdf_stats.set_index("Quality")["Win Rate %"]
            st.bar_chart(_chart_data, use_container_width=True)

            # ── Full stats table ──────────────────────────────────────────────
            st.markdown("#### Full Quality Stats")

            def _fmt_winrate(v):
                colour = "#22c55e" if v >= 50 else "#ef4444"
                return f"color: {colour}; font-weight: 600"

            def _fmt_pnl(v):
                colour = "#22c55e" if v >= 0 else "#ef4444"
                return f"color: {colour}"

            _qstyle = (
                _qdf_stats.style
                .applymap(_fmt_winrate,  subset=["Win Rate %"])
                .applymap(_fmt_pnl,      subset=["Avg PnL %"])
                .format({
                    "Win Rate %":    "{:.1f}%",
                    "Avg PnL %":     "{:+.2f}%",
                    "Avg Hold Days": "{:.1f}d",
                })
            )
            st.dataframe(_qstyle, use_container_width=True, hide_index=True)

            # ── Insight caption ───────────────────────────────────────────────
            if len(_quality_stats) >= 2:
                _best = max(_quality_stats, key=lambda x: x["Win Rate %"])
                st.caption(
                    f"💡 **{_best['Quality']}** setups achieve the highest win rate "
                    f"({_best['Win Rate %']:.1f}%) across {_best['Trades']} trades "
                    f"with an average hold of {_best['Avg Hold Days']:.0f} days."
                )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — Parameter Sweep
# ─────────────────────────────────────────────────────────────────────────────

with _tab_sweep:
    _sweep_csv_path = str(_sweep_path(_selected_csv))
    _sweep_df       = _load_sweep_csv(_sweep_csv_path)

    if _sweep_df is None or _sweep_df.empty:
        st.info(
            "No parameter sweep data found for this run.  \n\n"
            "Re-run the backtest with the `--sweep` flag to see a heatmap of "
            "`trailing_stop_pct` × `fixed_stop_pct` vs Sharpe / CAGR here:\n\n"
            "```\n"
            "python scripts/backtest_runner.py \\\n"
            "    --start 2019-01-01 --end 2024-01-01 \\\n"
            "    --sweep --universe nifty500\n"
            "```"
        )
    else:
        st.markdown("#### Parameter Sweep — Sharpe Ratio Heatmap")

        # ── Detect column names (flexible to engine output) ───────────────────
        _ts_col  = next((c for c in _sweep_df.columns
                         if "trailing" in c.lower()), None)
        _fs_col  = next((c for c in _sweep_df.columns
                         if "fixed" in c.lower()), None)
        _sh_col  = next((c for c in _sweep_df.columns
                         if "sharpe" in c.lower()), None)
        _cg_col  = next((c for c in _sweep_df.columns
                         if "cagr" in c.lower()), None)

        if _ts_col and _sh_col:
            # ── Sharpe heatmap (pivot: trailing_stop × fixed_stop or single col) ──
            try:
                if _fs_col:
                    _pivot_sharpe = _sweep_df.pivot_table(
                        index=_ts_col, columns=_fs_col,
                        values=_sh_col, aggfunc="mean",
                    )
                else:
                    _pivot_sharpe = _sweep_df[[_ts_col, _sh_col]].set_index(_ts_col)

                # Format trailing-stop index as percentages
                _pivot_sharpe.index = [
                    f"{float(v) * 100:.0f}% trailing"
                    if v is not None and str(v).lower() not in ("none", "nan")
                    else "Fixed only"
                    for v in _pivot_sharpe.index
                ]
                if _fs_col:
                    _pivot_sharpe.columns = [
                        f"{float(c) * 100:.0f}% fixed"
                        if str(c).lower() not in ("none", "nan")
                        else "No fixed"
                        for c in _pivot_sharpe.columns
                    ]

                st.dataframe(
                    _pivot_sharpe.style.background_gradient(
                        cmap="RdYlGn", axis=None
                    ).format("{:.2f}"),
                    use_container_width=True,
                )
            except Exception as _pe:
                st.warning(f"Could not pivot sweep table: {_pe}")
                st.dataframe(_sweep_df, use_container_width=True)

            # ── CAGR heatmap ──────────────────────────────────────────────────
            if _cg_col:
                st.markdown("#### Parameter Sweep — CAGR % Heatmap")
                try:
                    if _fs_col:
                        _pivot_cagr = _sweep_df.pivot_table(
                            index=_ts_col, columns=_fs_col,
                            values=_cg_col, aggfunc="mean",
                        )
                    else:
                        _pivot_cagr = _sweep_df[[_ts_col, _cg_col]].set_index(_ts_col)

                    _pivot_cagr.index = [
                        f"{float(v) * 100:.0f}% trailing"
                        if v is not None and str(v).lower() not in ("none", "nan")
                        else "Fixed only"
                        for v in _pivot_cagr.index
                    ]
                    st.dataframe(
                        _pivot_cagr.style.background_gradient(
                            cmap="RdYlGn", axis=None
                        ).format("{:.2f}%"),
                        use_container_width=True,
                    )
                except Exception:
                    pass

            # ── Raw sweep table ───────────────────────────────────────────────
            with st.expander("📄 Raw sweep data", expanded=False):
                st.dataframe(_sweep_df, use_container_width=True)
        else:
            # Couldn't identify columns — show raw table
            st.info(
                "Sweep CSV found but column names don't match expected pattern. "
                "Showing raw data:"
            )
            st.dataframe(_sweep_df, use_container_width=True)


st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Disclaimer
# ═════════════════════════════════════════════════════════════════════════════

st.caption(
    "⚠️ Past performance does not guarantee future results. "
    "Paper trade for 4–8 weeks before considering live execution. "
    "Backtest results do not account for slippage, brokerage costs, "
    "or liquidity constraints on smaller-cap NSE stocks."
)
