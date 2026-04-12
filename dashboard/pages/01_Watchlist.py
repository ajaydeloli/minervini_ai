"""
dashboard/pages/01_Watchlist.py
────────────────────────────────
Primary daily-use page: Watchlist management + Today's screening results.

Layout:
    ├── Page header  (title + market status bar)
    ├── Watchlist Manager  (file upload · manual entry · table · run trigger)
    ├── Today's Results  (watchlist setups first, then universe A+/A)
    └── Telegram Alert Preview

Run via:
    streamlit run dashboard/app.py --server.port 8501
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────────────────────
# Ensure project root is on sys.path (for shared helpers + components)
# ─────────────────────────────────────────────────────────────────────────────

_PAGE_DIR = Path(__file__).resolve().parent          # dashboard/pages/
_DASHBOARD_DIR = _PAGE_DIR.parent                    # dashboard/
_PROJECT_ROOT = _DASHBOARD_DIR.parent                # project root

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import requests
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Import shared helpers from app.py
# ─────────────────────────────────────────────────────────────────────────────

try:
    from dashboard.app import (
        get_todays_results,
        get_watchlist_symbols,
        is_market_open,
        load_db_path,
        get_latest_screen_date,
    )
except ImportError:
    # Fallback implementations so the page renders even when app.py is absent
    import sqlite3

    def load_db_path() -> str:  # type: ignore[misc]
        return str(_PROJECT_ROOT / "data" / "minervini.db")

    def is_market_open() -> bool:  # type: ignore[misc]
        IST = ZoneInfo("Asia/Kolkata")
        now = datetime.now(tz=IST)
        if now.weekday() >= 5:
            return False
        return now.replace(hour=9, minute=15, second=0, microsecond=0) <= now <= now.replace(hour=15, minute=30, second=0, microsecond=0)

    def get_latest_screen_date(db_path: str) -> str | None:  # type: ignore[misc]
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            row = conn.execute("SELECT MAX(run_date) FROM screener_results").fetchone()
            conn.close()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def get_watchlist_symbols(db_path: str) -> set[str]:  # type: ignore[misc]
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            rows = conn.execute("SELECT symbol FROM watchlist").fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def get_todays_results(db_path: str, date_str: str | None = None) -> list[dict]:  # type: ignore[misc]
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

# ─────────────────────────────────────────────────────────────────────────────
# Environment / API config
# ─────────────────────────────────────────────────────────────────────────────

_API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
_READ_KEY  = os.getenv("API_READ_KEY",  "")
_ADMIN_KEY = os.getenv("API_ADMIN_KEY", "")
_TIMEOUT   = 30

_DB_PATH = load_db_path()

IST = ZoneInfo("Asia/Kolkata")

# ─────────────────────────────────────────────────────────────────────────────
# API helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _read_headers() -> dict[str, str]:
    return {"X-API-Key": _READ_KEY} if _READ_KEY else {}


def _admin_headers() -> dict[str, str]:
    return {"X-API-Key": _ADMIN_KEY} if _ADMIN_KEY else {}


def _api_get_watchlist() -> list[dict]:
    """Fetch current watchlist from the API. Returns [] on error."""
    try:
        resp = requests.get(
            f"{_API_BASE}/api/v1/watchlist",
            headers=_read_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") or []
    except Exception as exc:
        st.error(f"⚠️ Could not load watchlist from API: {exc}")
        return []


def _api_bulk_add(symbols: list[str]) -> dict | None:
    """POST /api/v1/watchlist/bulk. Returns response data dict or None."""
    try:
        resp = requests.post(
            f"{_API_BASE}/api/v1/watchlist/bulk",
            json={"symbols": symbols},
            headers=_admin_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("message", str(exc))
        except Exception:
            detail = str(exc)
        st.error(f"API error: {detail}")
        return None
    except Exception as exc:
        st.error(f"⚠️ Could not reach API: {exc}")
        return None


def _api_delete_symbol(symbol: str) -> bool:
    """DELETE /api/v1/watchlist/{symbol}. Returns True on success."""
    try:
        resp = requests.delete(
            f"{_API_BASE}/api/v1/watchlist/{symbol}",
            headers=_admin_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("message", str(exc))
        except Exception:
            detail = str(exc)
        st.error(f"Remove failed: {detail}")
        return False
    except Exception as exc:
        st.error(f"⚠️ Could not reach API: {exc}")
        return False


def _api_clear_watchlist() -> bool:
    """DELETE /api/v1/watchlist (clear all). Returns True on success."""
    try:
        resp = requests.delete(
            f"{_API_BASE}/api/v1/watchlist",
            headers=_admin_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        st.error(f"⚠️ Clear failed: {exc}")
        return False


def _api_run_watchlist() -> tuple[bool, str]:
    """POST /api/v1/run scope=watchlist. Returns (success, message)."""
    try:
        resp = requests.post(
            f"{_API_BASE}/api/v1/run",
            json={"scope": "watchlist"},
            headers=_admin_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        msg = resp.json().get("message", "Run accepted.")
        return True, msg
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("message", str(exc))
        except Exception:
            detail = str(exc)
        return False, detail
    except Exception as exc:
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Inline file-parsing helpers  (mirrors ingestion/universe_loader.py logic)
# ─────────────────────────────────────────────────────────────────────────────

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}$")


def _validate_symbol(sym: str) -> bool:
    return bool(_SYMBOL_RE.match(sym))


def _parse_uploaded_file(uploaded_file) -> tuple[list[str], str | None]:
    """
    Parse a Streamlit UploadedFile into a list of valid NSE symbols.
    Returns (symbols, error_message). On success error_message is None.
    """
    name = uploaded_file.name or ""
    suffix = Path(name).suffix.lower()
    raw_bytes = uploaded_file.getvalue()

    raw_symbols: list[str] = []

    try:
        if suffix == ".txt":
            text = raw_bytes.decode("utf-8-sig", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                for part in stripped.split(","):
                    v = part.strip()
                    if v:
                        raw_symbols.append(v)

        elif suffix == ".csv":
            text = raw_bytes.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            fieldnames = reader.fieldnames or []
            sym_col = next((f for f in fieldnames if f.strip().lower() == "symbol"), None)
            if sym_col:
                for row in reader:
                    v = row.get(sym_col, "").strip()
                    if v:
                        raw_symbols.append(v)
            else:
                reader2 = csv.reader(io.StringIO(text))
                for i, row in enumerate(reader2):
                    if i == 0 and row and row[0].strip().lower() in ("symbol", "ticker", "scrip"):
                        continue
                    if row:
                        raw_symbols.append(row[0].strip())

        elif suffix == ".json":
            data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            if isinstance(data, list):
                raw_symbols = [str(x) for x in data if x]
            elif isinstance(data, dict):
                items = data.get("symbols") or data.get("watchlist") or []
                raw_symbols = [str(x) for x in items if x]
            else:
                return [], "JSON must be an array of strings or an object with a 'symbols' key."

        elif suffix in (".xlsx", ".xls"):
            try:
                import openpyxl
            except ImportError:
                return [], "openpyxl is required to read .xlsx files. Install: pip install openpyxl"
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return [], "Excel file is empty."
            header = [str(c).strip().lower() if c else "" for c in rows[0]]
            col_idx = next((i for i, h in enumerate(header) if h == "symbol"), 0)
            start = 1 if header[col_idx] == "symbol" else 0
            for row in rows[start:]:
                if col_idx < len(row) and row[col_idx] is not None:
                    v = str(row[col_idx]).strip()
                    if v:
                        raw_symbols.append(v)
            wb.close()

        else:
            return [], f"Unsupported file type '{suffix}'. Use .csv, .json, .xlsx, or .txt"

    except Exception as exc:
        return [], f"Error reading file: {exc}"

    # Validate + deduplicate
    valid: list[str] = []
    seen: set[str] = set()
    for raw in raw_symbols:
        sym = str(raw).strip().upper()
        if not sym or sym in seen:
            continue
        if _validate_symbol(sym):
            seen.add(sym)
            valid.append(sym)

    if not valid:
        return [], "No valid NSE symbols found in the file."

    return valid, None


# ─────────────────────────────────────────────────────────────────────────────
# Telegram alert preview formatter
# ─────────────────────────────────────────────────────────────────────────────

def _build_alert_preview(results: list[dict], watchlist_syms: set[str]) -> str:
    """Build a plain-text Telegram-style alert preview string."""
    top = [r for r in results if r.get("setup_quality") in ("A+", "A")]
    if not top:
        return "No A+/A setups today — no alert would be sent."

    now_str = datetime.now(tz=IST).strftime("%d %b %Y %H:%M IST")
    lines = [
        f"📊 *SEPA Daily Alert* — {now_str}",
        f"{'─' * 38}",
    ]

    wl_hits = [r for r in top if r["symbol"] in watchlist_syms]
    uni_hits = [r for r in top if r["symbol"] not in watchlist_syms]

    if wl_hits:
        lines.append(f"\n⭐ *Watchlist Setups* ({len(wl_hits)})")
        for r in wl_hits:
            lines.append(_fmt_alert_row(r))

    if uni_hits:
        lines.append(f"\n🔭 *Universe Setups* ({len(uni_hits)})")
        for r in uni_hits[:10]:   # cap at 10 to keep preview readable
            lines.append(_fmt_alert_row(r))
        if len(uni_hits) > 10:
            lines.append(f"  … and {len(uni_hits) - 10} more")

    lines.append(f"\n{'─' * 38}")
    lines.append("_Generated by Minervini SEPA v1.5.0_")
    return "\n".join(lines)


def _fmt_alert_row(r: dict) -> str:
    sym     = r.get("symbol", "?")
    quality = r.get("setup_quality", "?")
    score   = r.get("score")
    entry   = r.get("entry_price")
    stop    = r.get("stop_loss")
    risk    = r.get("risk_pct")

    badge = {"A+": "🥇", "A": "🥈", "B": "🥉", "C": "⚪"}.get(quality, "")
    score_str = f"{score:.1f}" if score is not None else "—"
    entry_str = f"₹{entry:,.2f}" if entry else "—"
    stop_str  = f"₹{stop:,.2f}"  if stop  else "—"
    risk_str  = f"{risk:.1f}%"   if risk  else "—"

    return (
        f"  {badge} *{sym}*  [{quality}]  Score: {score_str}\n"
        f"     Entry: {entry_str}  |  Stop: {stop_str}  |  Risk: {risk_str}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Session state initialisation
# ─────────────────────────────────────────────────────────────────────────────

if "watchlist_cache" not in st.session_state:
    st.session_state["watchlist_cache"]: list[dict] = []
if "watchlist_loaded" not in st.session_state:
    st.session_state["watchlist_loaded"] = False
if "parsed_upload_symbols" not in st.session_state:
    st.session_state["parsed_upload_symbols"]: list[str] = []
if "confirm_clear" not in st.session_state:
    st.session_state["confirm_clear"] = False


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Page header + market status bar
# ═════════════════════════════════════════════════════════════════════════════

st.title("📋 Watchlist & Daily Results")

_open = is_market_open()
_status_icon  = "🟢" if _open else "🔴"
_status_label = "Market Open" if _open else "Market Closed"

_now_ist    = datetime.now(tz=IST)
_latest_date = get_latest_screen_date(_DB_PATH)

# Last run info from DB
import sqlite3 as _sqlite3
def _last_run_dt() -> str:
    try:
        conn = _sqlite3.connect(_DB_PATH, timeout=5)
        row = conn.execute(
            "SELECT run_date, finished_at FROM run_history WHERE status='success' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return f"{row[0]}  {row[1] or ''}".strip()
        return "No run yet"
    except Exception:
        return "Unavailable"

_col_status, _col_lastrun, _col_nextrun = st.columns(3)
_col_status.metric(
    "Market Status",
    f"{_status_icon} {_status_label}",
    help="NSE session: Mon–Fri 09:15–15:30 IST",
)
_col_lastrun.metric(
    "Last Run",
    _last_run_dt(),
    help="Most recent successful pipeline run",
)
_col_nextrun.metric(
    "Next Scheduled Run",
    "15:35 IST",
    help="Daily after-market run",
)

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Custom Watchlist Manager
# ═════════════════════════════════════════════════════════════════════════════

with st.expander("⚙️ Manage Watchlist", expanded=True):

    # ── A. File upload ───────────────────────────────────────────────────────
    st.markdown("#### 📁 Upload Symbols File")
    uploaded = st.file_uploader(
        "Upload watchlist (.csv / .json / .xlsx / .txt)",
        type=["csv", "json", "xlsx", "xls", "txt"],
        key="wl_file_upload",
        help="CSV with a 'symbol' column, JSON array, XLSX first sheet, or one-per-line TXT",
    )

    if uploaded is not None:
        symbols, err_msg = _parse_uploaded_file(uploaded)
        if err_msg:
            st.error(f"Parse error: {err_msg}")
            st.session_state["parsed_upload_symbols"] = []
        else:
            st.session_state["parsed_upload_symbols"] = symbols
            preview = ", ".join(symbols[:15])
            suffix = f" … (+{len(symbols)-15} more)" if len(symbols) > 15 else ""
            st.success(f"Found **{len(symbols)}** valid symbol(s): {preview}{suffix}")
    else:
        st.session_state["parsed_upload_symbols"] = []

    if st.session_state["parsed_upload_symbols"]:
        if st.button("➕ Add to Watchlist", key="btn_upload_add"):
            result = _api_bulk_add(st.session_state["parsed_upload_symbols"])
            if result is not None:
                added   = len(result.get("added", []))
                skipped = len(result.get("already_exists", []))
                invalid = len(result.get("invalid", []))
                st.toast(
                    f"✅ Added {added} · Skipped {skipped} (exists) · Invalid {invalid}",
                    icon="✅",
                )
                st.session_state["watchlist_loaded"] = False  # force reload
                st.rerun()

    st.divider()


    # ── B. Manual entry ──────────────────────────────────────────────────────
    st.markdown("#### ✏️ Manual Entry")
    _manual_col, _add_col = st.columns([4, 1])
    manual_input = _manual_col.text_input(
        "Add symbols (comma-separated)",
        placeholder="RELIANCE, TCS, DIXON",
        key="wl_manual_input",
        label_visibility="collapsed",
    )
    with _add_col:
        st.write("")   # vertical alignment spacer
        if st.button("➕ Add", key="btn_manual_add"):
            if not manual_input.strip():
                st.toast("⚠️ Enter at least one symbol.", icon="⚠️")
            else:
                raw_syms = [s.strip().upper() for s in manual_input.split(",") if s.strip()]
                valid_syms   = [s for s in raw_syms if _validate_symbol(s)]
                invalid_syms = [s for s in raw_syms if not _validate_symbol(s)]
                if invalid_syms:
                    st.warning(f"Invalid symbols skipped: {', '.join(invalid_syms)}")
                if valid_syms:
                    result = _api_bulk_add(valid_syms)
                    if result is not None:
                        added = len(result.get("added", []))
                        st.toast(f"✅ Added {added} symbol(s) to watchlist.", icon="✅")
                        st.session_state["watchlist_loaded"] = False
                        st.rerun()

    st.divider()

    # ── C. Current watchlist table ───────────────────────────────────────────
    st.markdown("#### 📋 Current Watchlist")

    # Load from API (or use cache)
    if not st.session_state["watchlist_loaded"]:
        entries = _api_get_watchlist()
        st.session_state["watchlist_cache"]  = entries
        st.session_state["watchlist_loaded"] = True
    else:
        entries = st.session_state["watchlist_cache"]

    if not entries:
        st.info("Watchlist is empty. Add symbols using the upload or manual entry above.")
    else:
        import pandas as pd

        _df = pd.DataFrame(entries)
        # Normalise column names — API returns camelCase from Pydantic; map to readable labels
        _col_map = {
            "symbol":        "Symbol",
            "last_quality":  "Quality",
            "last_score":    "Score",
            "note":          "Note",
            "added_at":      "Added",
            "added_via":     "Via",
        }
        _visible = [c for c in _col_map if c in _df.columns]
        _df_show = _df[_visible].rename(columns=_col_map).copy()

        # Format: truncate added_at to date portion
        if "Added" in _df_show.columns:
            _df_show["Added"] = _df_show["Added"].astype(str).str[:10]

        # Add a "Remove" checkbox column via data_editor
        _df_show.insert(0, "✕ Remove", False)

        edited = st.data_editor(
            _df_show,
            use_container_width=True,
            hide_index=True,
            column_config={
                "✕ Remove": st.column_config.CheckboxColumn(
                    "Remove?",
                    help="Check to mark for deletion, then press Remove Selected",
                    default=False,
                    width="small",
                ),
                "Symbol":  st.column_config.TextColumn("Symbol",  width="small"),
                "Quality": st.column_config.TextColumn("Quality", width="small"),
                "Score":   st.column_config.NumberColumn("Score", format="%.1f", width="small"),
                "Added":   st.column_config.TextColumn("Added",   width="small"),
                "Via":     st.column_config.TextColumn("Via",     width="small"),
            },
            disabled=[c for c in _df_show.columns if c != "✕ Remove"],
            key="wl_data_editor",
        )

        _remove_col, _clear_col, _spacer = st.columns([2, 2, 4])

        with _remove_col:
            if st.button("🗑️ Remove Selected", key="btn_remove_sel"):
                to_remove = edited.loc[edited["✕ Remove"] == True, "Symbol"].tolist()
                if not to_remove:
                    st.toast("No rows selected for removal.", icon="ℹ️")
                else:
                    ok_count = 0
                    for sym in to_remove:
                        if _api_delete_symbol(sym):
                            ok_count += 1
                    if ok_count:
                        st.toast(f"Removed {ok_count} symbol(s).", icon="🗑️")
                        st.session_state["watchlist_loaded"] = False
                        st.rerun()

        with _clear_col:
            if not st.session_state["confirm_clear"]:
                if st.button("🚨 Clear All", key="btn_clear_all", type="secondary"):
                    st.session_state["confirm_clear"] = True
                    st.rerun()
            else:
                st.warning("This will remove **all** watchlist symbols. Are you sure?")
                _yes_col, _no_col = st.columns(2)
                with _yes_col:
                    if st.button("✅ Yes, clear", key="btn_clear_confirm"):
                        if _api_clear_watchlist():
                            st.toast("Watchlist cleared.", icon="🗑️")
                        st.session_state["confirm_clear"] = False
                        st.session_state["watchlist_loaded"] = False
                        st.rerun()
                with _no_col:
                    if st.button("❌ Cancel", key="btn_clear_cancel"):
                        st.session_state["confirm_clear"] = False
                        st.rerun()

    st.divider()


    # ── D. Run trigger ───────────────────────────────────────────────────────
    st.markdown("#### ▶ Run Watchlist Analysis")
    if st.button("▶ Run Watchlist Now", type="primary", key="btn_run_now"):
        with st.spinner("Running watchlist analysis… this may take 30–120 s…"):
            success, msg = _api_run_watchlist()
        if success:
            st.success(f"✅ Run complete! {msg}  Reloading results…")
            st.session_state["watchlist_loaded"] = False
            st.rerun()
        else:
            st.error(f"Run failed: {msg}")

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Today's Results
# ═════════════════════════════════════════════════════════════════════════════

st.header("📊 Today's Results")

if not _latest_date:
    st.info("No screening results available. Run the pipeline to populate results.")
else:
    st.caption(f"Showing results for **{_latest_date}**")

    # Load all results + watchlist symbols
    _all_results    = get_todays_results(_DB_PATH, _latest_date)
    _wl_symbols     = get_watchlist_symbols(_DB_PATH)

    # Split into watchlist setups vs universe A+/A setups
    _wl_results     = [r for r in _all_results if r.get("symbol") in _wl_symbols]
    _uni_results    = [
        r for r in _all_results
        if r.get("setup_quality") in ("A+", "A")
        and r.get("symbol") not in _wl_symbols
    ]

    # ── Watchlist setups ─────────────────────────────────────────────────────
    st.subheader(f"⭐ Watchlist Setups  ({len(_wl_results)})")

    if not _wl_results:
        st.info("No watchlist symbols appear in today's results.")
    else:
        try:
            from dashboard.components.tables import render_sepa_results_table
            render_sepa_results_table(
                results=_wl_results,
                watchlist_symbols=_wl_symbols,
            )
        except ImportError:
            # Graceful fallback if component hasn't been built yet
            import pandas as pd
            _COLS = ["symbol", "setup_quality", "score", "entry_price",
                     "stop_loss", "risk_pct", "rs_rating", "stage"]
            _df_wl = pd.DataFrame(_wl_results)
            _vis = [c for c in _COLS if c in _df_wl.columns]
            st.dataframe(_df_wl[_vis], use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(f"Could not render watchlist results table: {exc}")

    st.divider()

    # ── Universe A+/A setups ─────────────────────────────────────────────────
    st.subheader(f"🔭 Universe A+/A Setups  ({len(_uni_results)})")

    if not _uni_results:
        st.info("No A+/A setups in today's universe scan (outside watchlist).")
    else:
        try:
            from dashboard.components.tables import render_sepa_results_table
            render_sepa_results_table(
                results=_uni_results,
                watchlist_symbols=_wl_symbols,
            )
        except ImportError:
            import pandas as pd
            _COLS = ["symbol", "setup_quality", "score", "entry_price",
                     "stop_loss", "risk_pct", "rs_rating", "stage"]
            _df_uni = pd.DataFrame(_uni_results)
            _vis = [c for c in _COLS if c in _df_uni.columns]
            st.dataframe(_df_uni[_vis], use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(f"Could not render universe results table: {exc}")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Telegram Alert Preview
# ═════════════════════════════════════════════════════════════════════════════

with st.expander("📱 Alert Preview", expanded=False):
    st.markdown(
        "<p style='color:#8b949e; font-size:0.85rem; margin-top:-0.25rem;'>"
        "Preview of what the Telegram alert message would look like — "
        "no message is sent from this page.</p>",
        unsafe_allow_html=True,
    )

    if not _latest_date:
        st.info("No results to preview. Run the pipeline first.")
    else:
        _preview_results = _all_results if "_all_results" in dir() else get_todays_results(_DB_PATH)
        _preview_wl      = _wl_symbols  if "_wl_symbols"  in dir() else get_watchlist_symbols(_DB_PATH)
        _preview_text    = _build_alert_preview(_preview_results, _preview_wl)

        st.code(_preview_text, language=None)

        _aplus_count = sum(1 for r in _preview_results if r.get("setup_quality") == "A+")
        _a_count     = sum(1 for r in _preview_results if r.get("setup_quality") == "A")
        _pcol1, _pcol2, _pcol3 = st.columns(3)
        _pcol1.metric("A+ Setups in Alert", _aplus_count)
        _pcol2.metric("A Setups in Alert",  _a_count)
        _pcol3.metric("Watchlist Hits",     len([r for r in _preview_results if r.get("symbol") in _preview_wl and r.get("setup_quality") in ("A+", "A")]))
