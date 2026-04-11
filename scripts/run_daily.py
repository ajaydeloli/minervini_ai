"""
scripts/run_daily.py
────────────────────
CLI entry point for the Minervini AI daily pipeline run.

Delegates all pipeline work to pipeline/runner.py, which orchestrates:
  1. Feature computation (bootstrap or incremental update per symbol)
  2. SEPA screen (run_screen → screener/pipeline.py, parallel ProcessPoolExecutor)
  3. Persistence (sepa_results table + screener_results table in SQLite)
  4. Daily watchlist report (CSV + HTML via reports/daily_watchlist.py)
  5. Chart generation for A+/A setups (reports/chart_generator.py)
  6. Telegram alert dispatch (alerts/telegram_alert.py)
  7. Run history logging (storage/sqlite_store.finish_run)

Usage examples
──────────────
  # Full run for today (features → screen → CSV/HTML report → Telegram alert)
  python scripts/run_daily.py --date today

  # Specific past date (backfill / smoke-test)
  python scripts/run_daily.py --date 2024-01-15

  # Watchlist-only scan (skip full universe)
  python scripts/run_daily.py --date today --watchlist-only

  # Override with inline symbols
  python scripts/run_daily.py --symbols "RELIANCE,TCS,INFY" --dry-run

  # Load a watchlist file, persist to SQLite, then run
  python scripts/run_daily.py --watchlist /path/to/my_stocks.csv

  # Dry-run: resolve symbols + show feature plan without writing anything
  python scripts/run_daily.py --date today --dry-run

  # Custom DB and config paths
  python scripts/run_daily.py --db data/custom.db --config config/prod_settings.yaml
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Ensure the project root is on sys.path when the script is run directly,
# e.g.  python scripts/run_daily.py  (without  PYTHONPATH=. or pip install -e .)
# ─────────────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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
from pipeline.context import RunContext
from pipeline.runner import run as pipeline_run
from storage.sqlite_store import init_db
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
        description="Minervini AI — daily pipeline entry point (features → screen → reports → alerts).",
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
    parser.add_argument(
        "--skip-features",
        action="store_true",
        default=False,
        help=(
            "In --dry-run mode: skip the feature bootstrap/update plan preview. "
            "Has no effect on live runs (pipeline/runner.py always computes features)."
        ),
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
    Daily pipeline entry point — delegates to pipeline/runner.py.

    Flow:
        1.  Parse CLI arguments.
        2.  Resolve run date (warn on non-trading days, never abort).
        3.  Parse --symbols / --watchlist flags.
        4.  Resolve symbols via ingestion.universe_loader.resolve_symbols().
        5.  Print pre-run summary table (symbol counts, scope, date).
        6.  --dry-run: print symbol list + feature plan, then exit 0.
        7.  Load config/settings.yaml.
        8.  Build RunContext (run_date, scope, config, db_path, CLI overrides).
        9.  Delegate to pipeline.runner.run(context):
              • feature computation (bootstrap or incremental update)
              • SEPA screen (run_screen + persist_results + save_results)
              • reports/daily_watchlist.py  → CSV + HTML
              • reports/chart_generator.py → chart PNGs for A+/A setups
              • alerts/telegram_alert.py   → Telegram summary
              • finish_run() → auditable run_history entry
       10.  Print final summary from RunResult.

    Exit codes:
        0 — success (or --dry-run)
        1 — known domain error (WatchlistParseError, UniverseLoadError,
                                InvalidSymbolError, config load failure)
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

        # --- PHASE 2: FEATURE COMPUTATION (dry-run preview) ---
        if not args.skip_features:
            import yaml
            from features.feature_store import needs_bootstrap

            try:
                with open(args.config_path, encoding="utf-8") as _fh:
                    _dry_config: dict = yaml.safe_load(_fh) or {}
            except (OSError, Exception) as _exc:
                log.warning(
                    "Could not load settings.yaml — skipping feature dry-run plan",
                    reason=str(_exc),
                )
                _dry_config = {}

            if _dry_config:
                symbols_to_scan = run_symbols.symbols_to_scan
                n_would_bootstrap = 0
                n_would_update = 0
                print("Feature computation plan (dry-run):")
                for symbol in symbols_to_scan:
                    if needs_bootstrap(symbol, _dry_config):
                        print(f"  [DRY-RUN] {symbol}: would run feature bootstrap")
                        n_would_bootstrap += 1
                    else:
                        print(f"  [DRY-RUN] {symbol}: would run feature update ({run_date})")
                        n_would_update += 1
                print(
                    f"\nFeature plan: {n_would_bootstrap} bootstrap, "
                    f"{n_would_update} update"
                )
                log.info(
                    "Dry-run feature plan",
                    would_bootstrap=n_would_bootstrap,
                    would_update=n_would_update,
                )
        # --- END PHASE 2 (dry-run preview) ---

        log.info("Dry run complete — no DB writes performed", date=str(run_date))
        sys.exit(0)

    # ── Step 8: Initialise DB and hand off to pipeline runner ────────────────
    import yaml

    try:
        with open(args.config_path, encoding="utf-8") as _fh:
            app_config: dict = yaml.safe_load(_fh) or {}
    except (OSError, Exception) as _exc:
        print(
            f"ERROR: Could not load config from {args.config_path}: {_exc}",
            file=sys.stderr,
        )
        log.error("Config load failed", path=args.config_path, reason=str(_exc))
        sys.exit(1)

    # init_db here ensures the schema exists before the runner's Step 3 runs it
    # (idempotent — safe to call twice).
    init_db(args.db_path)

    context = RunContext(
        run_date=run_date,
        mode="daily",
        scope=scope,  # type: ignore[arg-type]
        config=app_config,
        db_path=Path(args.db_path),
        dry_run=False,
        cli_symbols=cli_symbols,
        cli_watchlist_file=cli_watchlist_file,
    )

    run_result = pipeline_run(context)

    # ── Step 9: Print final summary from RunResult ───────────────────────────
    rule = "─" * SEPARATOR_WIDTH
    print()
    print(rule)
    print("Run complete")
    print(rule)
    print(f"  {'Symbols screened':<24}: {run_result.symbols_screened}")
    print(f"  {'Passed Stage 2':<24}: {run_result.passed_stage2}")
    print(f"  {'Passed Trend Template':<24}: {run_result.passed_tt}")
    print(f"  {'VCP qualified':<24}: {run_result.vcp_qualified}")
    print(f"  {'A+ setups':<24}: {run_result.a_plus_count}")
    print(f"  {'A setups':<24}: {run_result.a_count}")
    print(f"  {'Status':<24}: {run_result.status}")
    print(f"  {'Duration':<24}: {run_result.duration_sec:.2f}s")
    if run_result.csv_path:
        print(f"  {'Watchlist CSV':<24}: {run_result.csv_path}")
    if run_result.html_path:
        print(f"  {'Watchlist HTML':<24}: {run_result.html_path}")
    print(f"  {'Alert sent':<24}: {run_result.alert_sent}")
    print(rule)

    log.info(
        "Daily run finished",
        symbols=run_result.symbols_screened,
        status=run_result.status,
        duration_sec=run_result.duration_sec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level guard
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
