#!/usr/bin/env python3
"""
scripts/show_run_history.py
────────────────────────────
CLI helper — print recent pipeline runs from the run_history table.

Usage
─────
    python scripts/show_run_history.py               # last 10 runs
    python scripts/show_run_history.py --n 30        # last N runs
    python scripts/show_run_history.py --date 2026-04-11  # runs for one date

Output columns
──────────────
    date | mode | status | symbols | A+ | A | duration | git_sha
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── make project root importable when called directly ────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _fmt_duration(sec: float | None) -> str:
    """Format seconds as 'm:ss' or '–' when None."""
    if sec is None:
        return "–"
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _status_badge(status: str) -> str:
    """Return a short coloured status label for terminal output."""
    colours = {
        "success": "\033[32m",   # green
        "partial": "\033[33m",   # yellow
        "failed":  "\033[31m",   # red
        "running": "\033[36m",   # cyan
    }
    reset = "\033[0m"
    colour = colours.get(status, "")
    return f"{colour}{status:<7}{reset}"


def _print_table(rows: list[dict]) -> None:
    """Render *rows* as a fixed-width table to stdout."""
    if not rows:
        print("No runs found.")
        return

    header = (
        f"{'date':<12} {'mode':<9} {'status':<7} "
        f"{'symbols':>7} {'A+':>4} {'A':>4} "
        f"{'duration':>8}  {'git_sha':<10}"
    )
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)

    for r in rows:
        date_s    = str(r.get("run_date", ""))[:10]
        mode_s    = str(r.get("run_mode", ""))[:9]
        status_s  = _status_badge(r.get("status", ""))
        symbols_s = str(r.get("universe_size") or "–")
        aplus_s   = str(r.get("a_plus_count") or 0)
        a_s       = str(r.get("a_count") or 0)
        dur_s     = _fmt_duration(r.get("duration_sec"))
        sha_s     = str(r.get("git_sha") or "–")[:10]

        print(
            f"{date_s:<12} {mode_s:<9} {status_s} "
            f"{symbols_s:>7} {aplus_s:>4} {a_s:>4} "
            f"{dur_s:>8}  {sha_s:<10}"
        )
    print(sep)
    print(f"  {len(rows)} run(s) shown.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show Minervini AI pipeline run history.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--n",
        type=int,
        default=10,
        metavar="N",
        help="Number of recent runs to show (default: 10).",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Filter to runs for a specific date.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/minervini.db"),
        metavar="PATH",
        help="Path to SQLite database (default: data/minervini.db).",
    )
    args = parser.parse_args()

    # ── init DB (read-only intent; init_db is idempotent) ────────────────────
    import storage.sqlite_store as ss

    ss.init_db(args.db)

    # ── fetch rows ────────────────────────────────────────────────────────────
    if args.date:
        # get_run_history doesn't support date filtering — query directly
        import sqlite3

        with ss._connect() as conn:  # noqa: SLF001
            rows_raw = conn.execute(
                "SELECT * FROM run_history WHERE run_date = ? ORDER BY id DESC",
                (args.date,),
            ).fetchall()
        rows = [dict(r) for r in rows_raw]
    else:
        rows = ss.get_run_history(limit=args.n)

    _print_table(rows)


if __name__ == "__main__":
    main()
