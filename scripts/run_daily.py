"""
scripts/run_daily.py
────────────────────
CLI entry point for the Minervini AI daily pipeline run.

At Phase 1 this script resolves the symbol universe, initialises the
SQLite database, logs the run, and prints a dry-run summary.

Phase 2 (feature computation) and Phase 3 (SEPA screening) are wired in
later — clearly-marked TODOs are left in place as integration hooks.

Usage examples
──────────────
  # Dry-run with today's date (no DB writes, no pipeline steps)
  python scripts/run_daily.py --date today --dry-run

  # Full run against the whole universe
  python scripts/run_daily.py --date 2024-01-15

  # Watchlist-only scan
  python scripts/run_daily.py --date today --watchlist-only

  # Override with inline symbols
  python scripts/run_daily.py --symbols "RELIANCE,TCS,INFY" --dry-run

  # Load a watchlist file, persist to SQLite, then run
  python scripts/run_daily.py --watchlist /path/to/my_stocks.csv

  # Custom DB and config paths
  python scripts/run_daily.py --db data/custom.db --config config/prod_settings.yaml
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Named constants — all tuneable defaults live here, never buried in logic
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH: str = "config/settings.yaml"
DEFAULT_DB_PATH: str = "data/minervini.db"
DEFAULT_UNIVERSE_YAML: str = "config/universe.yaml"
DEFAULT_SCOPE: str = "all"
SEPARATOR_WIDTH: int = 53          # width of the summary box rule lines
BANNER_TITLE: str = "Minervini Daily Run"

# ─────────────────────────────────────────────────────────────────────────────
# Project imports
# ─────────────────────────────────────────────────────────────────────────────

from ingestion.universe_loader import RunSymbols, resolve_symbols
from ingestion.universe_loader import load_watchlist_file  # noqa: F401 (used by resolve_symbols internally)
from storage.sqlite_store import init_db, log_run, finish_run
from utils.date_utils import today_ist, is_trading_day
from utils.exceptions import (
    InvalidSymbolError,
    UniverseLoadError,
    WatchlistParseError,
)
from utils.logger import get_logger, setup_logging

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_daily.py",
        description="Minervini AI — daily pipeline entry point (Phase 1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--date",
        default="today",
        metavar="YYYY-MM-DD",
        help=(
            'Run date as an ISO string (e.g. 2024-01-15) or the literal "today". '
            'Defaults to today in IST.'
        ),
    )
    parser.add_argument(
        "--watchlist",
        dest="watchlist_path",
        metavar="PATH",
        default=None,
        help=(
            "Path to a watchlist file (.csv / .json / .xlsx / .txt). "
            "Parsed and persisted to SQLite before symbol resolution."
        ),
    )
    parser.add_argument(
        "--symbols",
        dest="symbols",
        metavar='"SYM1,SYM2"',
        default=None,
        help=(
            "Comma-separated inline symbols (e.g. \"RELIANCE,TCS\"). "
            "When provided, overrides all other symbol sources."
        ),
    )
    parser.add_argument(
        "--watchlist-only",
        action="store_true",
        default=False,
        help="Skip the full universe scan — process watchlist symbols only.",
    )
    parser.add_argument(
        "--scope",
        choices=["all", "universe", "watchlist"],
        default=DEFAULT_SCOPE,
        help=(
            'Symbol scope: "all" (default), "universe", or "watchlist". '
            '--watchlist-only forces scope="watchlist".'
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Resolve and print symbols but do NOT write to the DB or "
            "execute any pipeline steps."
        ),
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        metavar="PATH",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to settings.yaml (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        metavar="PATH",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database file (default: {DEFAULT_DB_PATH}).",
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_date(raw: str) -> date:
    """
    Parse the --date argument into a date object.

    Accepts:
        "today"      → today's date in IST
        "YYYY-MM-DD" → parsed directly

    Emits a WARNING if the resolved date is not an NSE trading day
    (weekend or declared holiday).  Does NOT abort — backfill runs on
    non-trading days are valid.

    Raises:
        SystemExit(1): If the string is neither "today" nor a valid ISO date.
    """
    if raw.strip().lower() == "today":
        resolved = today_ist()
        log.debug("Date resolved to today (IST)", date=str(resolved))
    else:
        try:
            resolved = date.fromisoformat(raw.strip())
        except ValueError:
            print(
                f"ERROR: --date '{raw}' is not a valid ISO date (YYYY-MM-DD) "
                'and is not the literal "today".',
                file=sys.stderr,
            )
            sys.exit(1)

    if not is_trading_day(resolved):
        day_name = resolved.strftime("%A")
        print(
            f"WARNING: {resolved.isoformat()} ({day_name}) is not an NSE trading day "
            "(weekend or declared holiday). Continuing — this is allowed for backfill.",
            file=sys.stderr,
        )
        log.warning(
            "Non-trading day selected",
            date=str(resolved),
            day_of_week=day_name,
        )

    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# Summary table helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(
    run_date: date,
    scope: str,
    run_symbols: RunSymbols,
    dry_run: bool,
) -> None:
    """
    Print the pre-run summary table to stdout.

    Example output:
        ── Minervini Daily Run ──────────────────────────────
        Date        : 2024-01-15 (Monday)
        Scope       : all
        Watchlist   : 5 symbols
        Universe    : 20 symbols
        Total       : 23 symbols (2 watchlist overlap)
        Dry run     : False
        ─────────────────────────────────────────────────────
    """
    rule = "─" * SEPARATOR_WIDTH
    title_rule = f"── {BANNER_TITLE} " + "─" * (SEPARATOR_WIDTH - len(BANNER_TITLE) - 4)

    wl_count = len(run_symbols.watchlist)
    uni_count = len(run_symbols.universe)
    all_count = len(run_symbols.all)

    # Overlap = symbols present in both watchlist and universe
    wl_set = set(run_symbols.watchlist)
    uni_set = set(run_symbols.universe)
    overlap = len(wl_set & uni_set)

    day_name = run_date.strftime("%A")
    date_str = f"{run_date.isoformat()} ({day_name})"

    total_label = f"{all_count} symbols"
    if overlap:
        total_label += f" ({overlap} watchlist overlap)"

    print(title_rule)
    print(f"{'Date':<12}: {date_str}")
    print(f"{'Scope':<12}: {scope}")
    print(f"{'Watchlist':<12}: {wl_count} symbols")
    print(f"{'Universe':<12}: {uni_count} symbols")
    print(f"{'Total':<12}: {total_label}")
    print(f"{'Dry run':<12}: {dry_run}")
    print(rule)


def _print_symbol_list(run_symbols: RunSymbols) -> None:
    """Print the full list of symbols that *would* be scanned."""
    symbols = run_symbols.symbols_to_scan
    print(f"\nSymbols to scan ({len(symbols)}):")
    for sym in symbols:
        print(f"  {sym}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Daily pipeline entry point.

    Flow:
        1. Parse CLI arguments.
        2. Resolve run date (warn on non-trading days, never abort).
        3. Resolve symbols via ingestion.universe_loader.resolve_symbols().
        4. Print pre-run summary table.
        5. If --dry-run: print symbol list and exit 0.
        6. Initialise SQLite DB, log run start.
        7. Iterate symbols_to_scan — print progress [N/Total] SYMBOL.
           (Phase 2 feature computation + Phase 3 screening go here.)
        8. Call finish_run(), print final summary.

    Exit codes:
        0 — success (or --dry-run)
        1 — known domain error (WatchlistParseError, UniverseLoadError,
                                InvalidSymbolError) — message printed to stderr
    """
    # ── Logging setup (must happen before any log call) ──────────────────────
    setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    # ── Step 1: Scope reconciliation ─────────────────────────────────────────
    scope: str = args.scope
    if args.watchlist_only:
        scope = "watchlist"
        log.debug("--watchlist-only flag set; forcing scope=watchlist")

    # ── Step 2: Parse --date ─────────────────────────────────────────────────
    run_date: date = _resolve_date(args.date)
    log.info("Daily run starting", date=str(run_date), scope=scope)

    # ── Step 3: Parse --symbols ───────────────────────────────────────────────
    cli_symbols: list[str] | None = None
    if args.symbols:
        cli_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        log.debug("CLI symbols parsed", count=len(cli_symbols), symbols=cli_symbols)

    # ── Step 4: Resolve watchlist file path ──────────────────────────────────
    cli_watchlist_file: Path | None = None
    if args.watchlist_path:
        cli_watchlist_file = Path(args.watchlist_path)

    # ── Step 5: Resolve symbols ───────────────────────────────────────────────
    try:
        run_symbols: RunSymbols = resolve_symbols(
            config_path=DEFAULT_UNIVERSE_YAML,
            cli_watchlist_file=cli_watchlist_file,
            cli_symbols=cli_symbols,
            scope=scope,  # type: ignore[arg-type]
        )
    except WatchlistParseError as exc:
        print(f"ERROR [WatchlistParseError]: {exc}", file=sys.stderr)
        log.error("Watchlist parse failed", reason=str(exc))
        sys.exit(1)
    except UniverseLoadError as exc:
        print(f"ERROR [UniverseLoadError]: {exc}", file=sys.stderr)
        log.error("Universe load failed", reason=str(exc))
        sys.exit(1)
    except InvalidSymbolError as exc:
        print(f"ERROR [InvalidSymbolError]: {exc}", file=sys.stderr)
        log.error("Invalid symbol in --symbols flag", reason=str(exc))
        sys.exit(1)

    # ── Step 6: Print pre-run summary ─────────────────────────────────────────
    _print_summary(
        run_date=run_date,
        scope=scope,
        run_symbols=run_symbols,
        dry_run=args.dry_run,
    )

    # ── Step 7: Dry-run fast exit ─────────────────────────────────────────────
    if args.dry_run:
        _print_symbol_list(run_symbols)
        log.info("Dry run complete — no DB writes performed", date=str(run_date))
        sys.exit(0)

    # ── Step 8: Initialise DB and log run start ───────────────────────────────
    init_db(args.db_path)

    run_id: int = log_run(
        run_date=run_date,
        run_mode="daily",
        scope=scope,
        universe_size=len(run_symbols.universe),
        watchlist_size=len(run_symbols.watchlist),
    )
    log.info("Run logged", run_id=run_id)

    # ── Step 9: Process symbols ───────────────────────────────────────────────
    symbols_to_scan = run_symbols.symbols_to_scan
    total = len(symbols_to_scan)
    wall_start = time.monotonic()

    try:
        for idx, symbol in enumerate(symbols_to_scan, start=1):
            print(f"[{idx}/{total}] [PENDING] {symbol}")
            log.debug("Symbol pending", symbol=symbol, idx=idx, total=total)

            # TODO Phase 2: feature_store.update(symbol, run_date, config)
            # TODO Phase 3: run_screen(symbol, run_date, config)

        elapsed = time.monotonic() - wall_start

        # ── Step 10: Mark run complete ────────────────────────────────────────
        finish_run(
            run_id=run_id,
            status="success",
            duration_sec=elapsed,
        )

    except Exception as exc:
        elapsed = time.monotonic() - wall_start
        log.error("Unexpected error during pipeline run", exc_info=True, run_id=run_id)
        finish_run(
            run_id=run_id,
            status="failed",
            duration_sec=elapsed,
            error_msg=str(exc),
        )
        raise

    # ── Step 11: Final summary ────────────────────────────────────────────────
    print()
    print(f"Run complete │ symbols processed: {total} │ elapsed: {elapsed:.2f}s")
    log.info(
        "Daily run finished",
        run_id=run_id,
        symbols=total,
        duration_sec=round(elapsed, 2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level guard
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
