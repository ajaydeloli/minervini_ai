"""
reports/daily_watchlist.py
──────────────────────────
Generate the daily SEPA watchlist report (CSV + HTML).

Public API
──────────
    generate_watchlist(run_date, results, config,
                       output_dir="reports/output",
                       watchlist_symbols=None) -> WatchlistOutput

WatchlistOutput
───────────────
    csv_path      : Path  – written CSV file
    html_path     : Path  – rendered HTML file
    a_plus_count  : int
    a_count       : int
    total_count   : int

Usage
─────
    from reports.daily_watchlist import generate_watchlist
    output = generate_watchlist(
        run_date="2024-01-15",
        results=sepa_results,   # list[SEPAResult] or list[dict]
        config=config,
        watchlist_symbols={"DIXON", "RELIANCE"},
    )
    print(output.csv_path, output.html_path)
"""

from __future__ import annotations

import csv
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, Set

from jinja2 import Environment, FileSystemLoader, select_autoescape

from utils.exceptions import MinerviniError
from utils.logger import get_logger

log = get_logger(__name__)

# ── Template directory (sibling to this file) ─────────────────────────────────
_TEMPLATE_DIR = Path(__file__).parent / "templates"

# ── CSV column order ──────────────────────────────────────────────────────────
_CSV_COLUMNS = [
    "rank",
    "symbol",
    "score",
    "setup_quality",
    "stage",
    "rs_rating",
    "vcp_qualified",
    "breakout_triggered",
    "entry_price",
    "stop_loss",
    "risk_pct",
    "in_watchlist",
    "fundamental_pass",
    "fundamental_details",
    "news_score",
]


# ─────────────────────────────────────────────────────────────────────────────
# Output dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WatchlistOutput:
    """Paths and summary statistics produced by generate_watchlist()."""

    csv_path: Path
    html_path: Path
    a_plus_count: int
    a_count: int
    total_count: int


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(
    results: Sequence[Any],
) -> list[dict[str, Any]]:
    """
    Accept list[SEPAResult] or list[dict] and always return list[dict].

    SEPAResult objects are converted via rules.scorer.to_dict().
    Plain dicts are returned as-is (shallow copy).
    """
    from rules.scorer import SEPAResult, to_dict  # local import avoids circular

    normalised: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, SEPAResult):
            normalised.append(to_dict(item))
        elif isinstance(item, dict):
            normalised.append(dict(item))
        else:
            raise MinerviniError(
                f"generate_watchlist: unsupported result type {type(item).__name__}"
            )
    return normalised


def _is_watchlist(row: dict[str, Any], watchlist_symbols: Set[str] | None) -> bool:
    """
    Determine whether *row* belongs to the user watchlist.

    Priority:
      1. ``in_watchlist`` field already present in the row (from SQLite).
      2. Symbol present in the caller-supplied *watchlist_symbols* set.
    """
    if "in_watchlist" in row and row["in_watchlist"] is not None:
        return bool(row["in_watchlist"])
    if watchlist_symbols is not None:
        return str(row.get("symbol", "")).upper() in watchlist_symbols
    return False


def _sort_results(
    rows: list[dict[str, Any]],
    watchlist_symbols: Set[str] | None,
    priority_in_reports: bool,
) -> list[dict[str, Any]]:
    """
    Sort rows by:
      1. Watchlist flag first (if priority_in_reports is True).
      2. Score descending within each group.
    """
    def _key(row: dict[str, Any]):
        wl = _is_watchlist(row, watchlist_symbols)
        score = row.get("score") or 0
        if priority_in_reports:
            return (0 if wl else 1, -score)
        return (-score,)

    return sorted(rows, key=_key)


def _enrich(
    rows: list[dict[str, Any]],
    watchlist_symbols: Set[str] | None,
) -> list[dict[str, Any]]:
    """Stamp each row with a normalised ``in_watchlist`` bool and ``rank``."""
    enriched = []
    for rank, row in enumerate(rows, start=1):
        r = dict(row)
        r["in_watchlist"] = _is_watchlist(row, watchlist_symbols)
        r["rank"] = rank
        enriched.append(r)
    return enriched


def _write_csv(
    rows: list[dict[str, Any]],
    path: Path,
) -> None:
    """Write *rows* as a CSV file with the canonical column set."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=_CSV_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    log.info("Watchlist CSV written", path=str(path), rows=len(rows))


def _render_html(
    rows: list[dict[str, Any]],
    run_date: str,
    a_plus_count: int,
    a_count: int,
    total_count: int,
    generated_at: str,
    path: Path,
) -> None:
    """Render the Jinja2 HTML template and write to *path*."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("watchlist.html.j2")
    html = template.render(
        run_date=run_date,
        results=rows,
        a_plus_count=a_plus_count,
        a_count=a_count,
        total_count=total_count,
        generated_at=generated_at,
    )
    path.write_text(html, encoding="utf-8")
    log.info("Watchlist HTML written", path=str(path))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_watchlist(
    run_date: str | datetime.date,
    results: Sequence[Any],
    config: dict[str, Any],
    output_dir: str | Path = "reports/output",
    watchlist_symbols: Set[str] | None = None,
) -> WatchlistOutput:
    """
    Generate the daily SEPA watchlist report as CSV + HTML.

    Parameters
    ──────────
    run_date          : Date of the screen run ("YYYY-MM-DD" string or date).
    results           : list[SEPAResult] or list[dict] from the screener.
    config            : Full application config dict (from settings.yaml).
    output_dir        : Directory where files are written. Created if absent.
    watchlist_symbols : Optional set of watchlist ticker symbols.  Used to
                        mark rows when the ``in_watchlist`` field is absent
                        (e.g. results passed directly from the screener).

    Returns
    ───────
    WatchlistOutput with paths and A+/A/total counts.

    Raises
    ──────
    MinerviniError    : On unsupported result type or template error.
    """
    # ── Normalise run_date to string ─────────────────────────────────────────
    if isinstance(run_date, datetime.date):
        date_str = run_date.isoformat()
    else:
        date_str = str(run_date)[:10]

    # ── Read config knob ──────────────────────────────────────────────────────
    priority_in_reports: bool = (
        config.get("watchlist", {}).get("priority_in_reports", True)
    )

    # ── Prepare output dir ────────────────────────────────────────────────────
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Normalise → sort → enrich ─────────────────────────────────────────────
    rows = _normalise(results)
    rows = _sort_results(rows, watchlist_symbols, priority_in_reports)
    rows = _enrich(rows, watchlist_symbols)

    # ── Summary counts ────────────────────────────────────────────────────────
    a_plus_count = sum(1 for r in rows if r.get("setup_quality") == "A+")
    a_count      = sum(1 for r in rows if r.get("setup_quality") == "A")
    total_count  = len(rows)

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── File paths ────────────────────────────────────────────────────────────
    csv_path  = out_dir / f"watchlist_{date_str}.csv"
    html_path = out_dir / f"watchlist_{date_str}.html"

    # ── Write files ───────────────────────────────────────────────────────────
    _write_csv(rows, csv_path)
    _render_html(
        rows=rows,
        run_date=date_str,
        a_plus_count=a_plus_count,
        a_count=a_count,
        total_count=total_count,
        generated_at=generated_at,
        path=html_path,
    )

    log.info(
        "Watchlist report generated",
        run_date=date_str,
        csv=str(csv_path),
        html=str(html_path),
        a_plus=a_plus_count,
        a=a_count,
        total=total_count,
    )

    return WatchlistOutput(
        csv_path=csv_path,
        html_path=html_path,
        a_plus_count=a_plus_count,
        a_count=a_count,
        total_count=total_count,
    )
