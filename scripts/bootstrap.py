"""
scripts/bootstrap.py
────────────────────
One-time (or periodic) full OHLCV history download for the Minervini AI system.

Downloads N years of OHLCV history for every symbol in the resolved universe,
validates it, and writes clean data to data/processed/{symbol}.parquet.

At Phase 1 this does NOT compute features — it only downloads and stores clean
OHLCV.  Feature computation (feature_store.bootstrap) is wired in Phase 2.

Usage examples
──────────────
  # Bootstrap the full universe from universe.yaml (default)
  python scripts/bootstrap.py

  # Equivalent explicit form — reads config/universe.yaml
  python scripts/bootstrap.py --universe config

  # Bootstrap the full Nifty 500 placeholder universe
  python scripts/bootstrap.py --universe nifty500

  # Bootstrap only the SQLite watchlist
  python scripts/bootstrap.py --watchlist-only

  # Load an external watchlist file, persist to SQLite, then bootstrap
  python scripts/bootstrap.py --watchlist /path/to/my_stocks.csv

  # Bootstrap specific inline symbols
  python scripts/bootstrap.py --symbols "RELIANCE,TCS,INFY"

  # Force re-download even when data already exists
  python scripts/bootstrap.py --force

  # Dry run — see what would be downloaded without writing anything
  python scripts/bootstrap.py --dry-run

  # 10 years of history, 8 parallel workers
  python scripts/bootstrap.py --years 10 --workers 8

  # Use a specific date as the "today" anchor (end of the download window)
  python scripts/bootstrap.py --universe config --date 2024-01-15

  # Universe + watchlist combined, custom paths
  python scripts/bootstrap.py --universe all --db data/custom.db --output-dir data/processed
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from dateutil.relativedelta import relativedelta

# ─────────────────────────────────────────────────────────────────────────────
# Named constants — all tuneable defaults live here, never buried in logic
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH: str = "config/settings.yaml"
DEFAULT_DB_PATH: str = "data/minervini.db"
DEFAULT_OUTPUT_DIR: str = "data/processed"
DEFAULT_YEARS: int = 5
DEFAULT_WORKERS: int = 4
MIN_ROWS_THRESHOLD: int = 200          # minimum rows to consider history sufficient
SEPARATOR_WIDTH: int = 53             # width of the summary box rule lines
BANNER_TITLE: str = "Minervini Bootstrap"

# ─────────────────────────────────────────────────────────────────────────────
# Project imports
# ─────────────────────────────────────────────────────────────────────────────

from ingestion.universe_loader import RunSymbols, resolve_symbols
from ingestion import get_data_source
from ingestion.validator import check_sufficient_history, validate
from storage.parquet_store import exists, row_count, write
from storage.sqlite_store import finish_run, init_db, log_run
from utils.date_utils import required_history_start, today_ist
from utils.exceptions import (
    DataValidationError,
    InsufficientDataError,
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
        prog="bootstrap.py",
        description="Minervini AI — full OHLCV history bootstrap (Phase 1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--universe",
        choices=["nifty500", "config", "all"],
        default="config",
        help=(
            'Universe source: "config" (default — reads config/universe.yaml), '
            '"nifty500" (Nifty 500 placeholder), '
            '"all" (universe + watchlist combined).'
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
        "--watchlist-only",
        action="store_true",
        default=False,
        help="Bootstrap only symbols currently in the SQLite watchlist.",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=DEFAULT_YEARS,
        metavar="N",
        help=f"Years of OHLCV history to download (default: {DEFAULT_YEARS}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        metavar="N",
        help=f"Parallel download threads (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            f"Re-download even if data/processed/{{symbol}}.parquet already "
            f"exists with >= {MIN_ROWS_THRESHOLD} rows."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be downloaded and exit — no files written.",
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
        "--output-dir",
        dest="output_dir",
        metavar="PATH",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for Parquet files (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--skip-features",
        action="store_true",
        default=False,
        help=(
            "Download and store OHLCV only — skip Phase 2 feature computation. "
            "Useful when you want to inspect raw data before committing to a full bootstrap."
        ),
    )
    parser.add_argument(
        "--date",
        dest="date",
        metavar="DATE",
        default="today",
        help=(
            'End-of-window anchor date in ISO format (YYYY-MM-DD) or "today" (default). '
            "Sets the right edge of the download window; --years counts back from this date. "
            "Example: --date 2024-01-15 downloads history ending on 2024-01-15."
        ),
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Summary helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(
    symbols: list[str],
    start_date: date,
    years: int,
    workers: int,
    output_dir: str,
    force: bool,
    dry_run: bool,
) -> None:
    """
    Print the pre-run summary table to stdout.

    Example output:
        ── Minervini Bootstrap ──────────────────────────────
        Symbols     : 20
        History     : 5 years (from 2019-04-04)
        Workers     : 4
        Output dir  : data/processed
        Force       : False
        Dry run     : False
        ──────────────────────────────────────────────────────
    """
    rule = "─" * SEPARATOR_WIDTH
    title_rule = f"── {BANNER_TITLE} " + "─" * (SEPARATOR_WIDTH - len(BANNER_TITLE) - 4)

    print(title_rule)
    print(f"{'Symbols':<12}: {len(symbols)}")
    print(f"{'History':<12}: {years} years (from {start_date.isoformat()})")
    print(f"{'Workers':<12}: {workers}")
    print(f"{'Output dir':<12}: {output_dir}")
    print(f"{'Force':<12}: {force}")
    print(f"{'Dry run':<12}: {dry_run}")
    print(rule)


def _print_symbol_list(symbols: list[str]) -> None:
    """Print the full list of symbols that would be bootstrapped."""
    print(f"\nSymbols to bootstrap ({len(symbols)}):")
    for sym in symbols:
        print(f"  {sym}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Per-symbol bootstrap worker
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap_symbol(
    symbol: str,
    idx: int,
    total: int,
    start_date: date,
    end_date: date,
    output_dir: Path,
    force: bool,
    print_lock: threading.Lock,
    config: dict,
) -> dict:
    """
    Download, validate, and persist OHLCV history for a single symbol.

    Called from ThreadPoolExecutor workers — must be thread-safe.
    All stdout output is serialised through *print_lock*.

    Returns a result dict with keys:
        status : "ok" | "skipped" | "failed"
        symbol : str
        rows   : int  (0 on failure, existing count on skip)
        reason : str  (empty when status == "ok")
    """
    parquet_path = output_dir / f"{symbol}.parquet"

    # ── Step a: Skip check ────────────────────────────────────────────────────
    if not force and exists(parquet_path):
        existing_rows = row_count(parquet_path)
        if existing_rows >= MIN_ROWS_THRESHOLD:
            with print_lock:
                print(f"[SKIP] {symbol} ({existing_rows} rows already present)")
            log.debug(
                "Symbol skipped — sufficient data already present",
                symbol=symbol,
                rows=existing_rows,
                path=str(parquet_path),
            )
            return {
                "status": "skipped",
                "symbol": symbol,
                "rows": existing_rows,
                "reason": "",
            }

    # ── Step b: Fetch ─────────────────────────────────────────────────────────
    try:
        df = get_data_source(config).fetch(symbol, start=start_date, end=end_date)
    except Exception as exc:
        with print_lock:
            print(f"[FAIL] {symbol} — fetch error: {exc}")
        log.warning(
            "Symbol fetch failed",
            symbol=symbol,
            error=str(exc),
        )
        return {
            "status": "failed",
            "symbol": symbol,
            "rows": 0,
            "reason": f"fetch error: {exc}",
        }

    # ── Step c: Validate ──────────────────────────────────────────────────────
    try:
        df = validate(df, symbol)
    except DataValidationError as exc:
        with print_lock:
            print(f"[FAIL] {symbol} — validation error: {exc}")
        log.warning(
            "Symbol validation failed",
            symbol=symbol,
            error=str(exc),
        )
        return {
            "status": "failed",
            "symbol": symbol,
            "rows": 0,
            "reason": f"validation error: {exc}",
        }

    # ── Step d: Sufficient history check ──────────────────────────────────────
    try:
        check_sufficient_history(df, symbol, min_rows=MIN_ROWS_THRESHOLD)
    except InsufficientDataError as exc:
        with print_lock:
            print(f"[FAIL] {symbol} — insufficient history: {exc}")
        log.warning(
            "Symbol has insufficient history after download",
            symbol=symbol,
            rows=len(df),
            required=MIN_ROWS_THRESHOLD,
            error=str(exc),
        )
        return {
            "status": "failed",
            "symbol": symbol,
            "rows": len(df),
            "reason": f"insufficient history: {exc}",
        }

    # ── Step e: Write to Parquet ──────────────────────────────────────────────
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        write(df, parquet_path, overwrite=True)
    except Exception as exc:
        with print_lock:
            print(f"[FAIL] {symbol} — write error: {exc}")
        log.warning(
            "Symbol Parquet write failed",
            symbol=symbol,
            path=str(parquet_path),
            error=str(exc),
        )
        return {
            "status": "failed",
            "symbol": symbol,
            "rows": len(df),
            "reason": f"write error: {exc}",
        }

    # ── Step f: Progress line ─────────────────────────────────────────────────
    rows = len(df)
    date_start = df.index[0].date()
    date_end   = df.index[-1].date()

    with print_lock:
        print(
            f"[{idx}/{total}] {symbol} — {rows} rows "
            f"({date_start} → {date_end}) ✓"
        )

    log.info(
        "Symbol bootstrapped",
        symbol=symbol,
        rows=rows,
        date_start=str(date_start),
        date_end=str(date_end),
        path=str(parquet_path),
    )

    return {
        "status": "ok",
        "symbol": symbol,
        "rows": rows,
        "reason": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Bootstrap entry point.

    Flow:
        0.  Resolve run_date from --date (ISO string or "today").
        1.  Parse CLI arguments.
        2.  Resolve the symbol list (--symbols overrides; --watchlist loads an
            external file and persists it to SQLite; --watchlist-only limits
            scope; --universe controls universe source).
        3.  Compute start_date = run_date - relativedelta(years=args.years).
        4.  Validate the output directory is reachable (fatal on failure).
        5.  Print pre-run summary table.
        6.  If --dry-run: print symbol list and exit 0.
        7.  Initialise SQLite DB and log run start.
        8.  Bootstrap each symbol in parallel using ThreadPoolExecutor.
            Per symbol: skip-check → fetch → validate → history-check → write.
        9.  Print final summary (downloaded / skipped / failed / elapsed).
        10. Call finish_run() with appropriate status.

    Exit codes:
        0 — success or dry-run
        1 — fatal error (bad --date, bad symbols, missing universe, unreachable
             output dir) or KeyboardInterrupt
    """
    # ── Logging setup (must happen before any log call) ──────────────────────
    setup_logging()

    # ── Startup guard: ensure all required data/ subdirectories exist ─────────
    _data_root = Path(__file__).resolve().parent.parent / "data"
    for _sub in (
        "raw", "processed", "features", "fundamentals", "news",
        "metadata", "benchmarks", "paper_trading", "charts", "reports",
    ):
        (_data_root / _sub).mkdir(parents=True, exist_ok=True)

    parser = _build_parser()
    args = parser.parse_args()

    # ── Step 0: Resolve run_date from --date flag ─────────────────────────────
    if args.date == "today":
        run_date: date = today_ist()
    else:
        try:
            run_date = date.fromisoformat(args.date)
        except ValueError:
            print(
                f"ERROR: --date value '{args.date}' is not a valid ISO date "
                "(expected YYYY-MM-DD or 'today').",
                file=sys.stderr,
            )
            sys.exit(1)

    log.info("Bootstrap starting", run_date=str(run_date))

    # ── Step 1: Parse --symbols ───────────────────────────────────────────────
    cli_symbols: list[str] | None = None
    if args.symbols:
        cli_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        log.debug("CLI symbols parsed", count=len(cli_symbols), symbols=cli_symbols)

    # ── Step 1b: Resolve watchlist file path ──────────────────────────────────
    cli_watchlist_file: Path | None = None
    if args.watchlist_path:
        cli_watchlist_file = Path(args.watchlist_path)
        log.debug("External watchlist file supplied", path=str(cli_watchlist_file))

    # ── Step 2: Determine scope ───────────────────────────────────────────────
    # Priority: --symbols > --watchlist-only > --universe
    if cli_symbols:
        scope = "all"   # resolve_symbols fast-paths on cli_symbols, scope ignored
    elif args.watchlist_only:
        scope = "watchlist"
        log.debug("--watchlist-only set; forcing scope=watchlist")
    elif args.universe == "all":
        scope = "all"
    else:
        # "config" and "nifty500" both resolve via universe.yaml
        scope = "universe"

    # ── Step 3: Resolve symbols ───────────────────────────────────────────────
    # Determine whether the --universe flag requests a specific fetch mode
    # (nifty500 / nse_all) that should override what's written in universe.yaml.
    universe_mode_override: str | None = (
        args.universe if args.universe in ("nifty500", "nse_all") else None
    )
    try:
        run_symbols: RunSymbols = resolve_symbols(
            config_path="config/universe.yaml",
            cli_watchlist_file=cli_watchlist_file,
            cli_symbols=cli_symbols,
            scope=scope,  # type: ignore[arg-type]
            universe_mode=universe_mode_override,
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

    symbols_to_bootstrap: list[str] = run_symbols.symbols_to_scan

    if not symbols_to_bootstrap:
        print(
            "ERROR: No symbols resolved. "
            "Check config/universe.yaml or use --symbols to specify symbols.",
            file=sys.stderr,
        )
        log.error("Empty symbol list — nothing to bootstrap")
        sys.exit(1)

    # ── Step 4: Compute date range ────────────────────────────────────────────
    end_date: date = run_date
    start_date: date = end_date - relativedelta(years=args.years)
    # Fallback (defensive): use required_history_start if relativedelta unavailable
    # start_date = required_history_start(args.years * 252, reference=end_date, buffer_pct=0.0)

    log.debug(
        "Date range computed",
        start=str(start_date),
        end=str(end_date),
        years=args.years,
    )

    # ── Step 5: Ensure output directory is reachable ──────────────────────────
    output_dir = Path(args.output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"ERROR: Cannot create output directory '{output_dir}': {exc}",
            file=sys.stderr,
        )
        log.error(
            "Output directory creation failed",
            path=str(output_dir),
            error=str(exc),
        )
        sys.exit(1)

    # ── Step 6: Print pre-run summary ─────────────────────────────────────────
    _print_summary(
        symbols=symbols_to_bootstrap,
        start_date=start_date,
        years=args.years,
        workers=args.workers,
        output_dir=args.output_dir,
        force=args.force,
        dry_run=args.dry_run,
    )

    # ── Step 7: Dry-run fast exit ─────────────────────────────────────────────
    if args.dry_run:
        _print_symbol_list(symbols_to_bootstrap)
        log.info("Dry run complete — no downloads or DB writes performed")
        sys.exit(0)

    # ── Step 8a: Load application config (needed by the data-source factory) ───
    import yaml as _yaml

    try:
        with open(args.config_path, encoding="utf-8") as _fh:
            app_config: dict = _yaml.safe_load(_fh) or {}
    except (OSError, Exception) as _cfg_exc:
        print(
            f"ERROR: Could not load config from '{args.config_path}': {_cfg_exc}",
            file=sys.stderr,
        )
        log.error("Config load failed", path=args.config_path, reason=str(_cfg_exc))
        sys.exit(1)

    _active_source = app_config.get("universe", {}).get("source", "yfinance")
    log.info("Data source resolved from config", source=_active_source)

    # ── Step 8b: Initialise DB and log run start ──────────────────────────────
    init_db(args.db_path)

    run_id: int = log_run(
        run_date=run_date,
        run_mode="manual",
        scope="bootstrap",
        universe_size=len(run_symbols.universe),
        watchlist_size=len(run_symbols.watchlist),
    )
    log.info("Bootstrap run logged", run_id=run_id)

    # ── Step 9: Parallel bootstrap ────────────────────────────────────────────
    total = len(symbols_to_bootstrap)
    wall_start = time.monotonic()
    print_lock = threading.Lock()

    # Pre-allocate result slots keyed by 0-based index for thread safety.
    # None = not yet processed (will count as failed in final tally).
    results: list[dict | None] = [None] * total

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            # Submit all tasks, map future → 0-based result index
            future_to_idx: dict = {
                executor.submit(
                    _bootstrap_symbol,
                    symbol,
                    idx,             # 1-based display index ([idx/total])
                    total,
                    start_date,
                    end_date,
                    output_dir,
                    args.force,
                    print_lock,
                    app_config,      # pass resolved config so factory reads universe.source
                ): (idx - 1)         # 0-based slot in results[]
                for idx, symbol in enumerate(symbols_to_bootstrap, start=1)
            }

            for future in as_completed(future_to_idx):
                result_idx = future_to_idx[future]
                try:
                    results[result_idx] = future.result()
                except Exception as exc:
                    sym = symbols_to_bootstrap[result_idx]
                    with print_lock:
                        print(f"[FAIL] {sym} — unexpected worker error: {exc}")
                    log.error(
                        "Unexpected error in bootstrap worker",
                        symbol=sym,
                        error=str(exc),
                        exc_info=True,
                    )
                    results[result_idx] = {
                        "status": "failed",
                        "symbol": sym,
                        "rows": 0,
                        "reason": f"unexpected worker error: {exc}",
                    }

    except KeyboardInterrupt:
        elapsed = time.monotonic() - wall_start
        print("\nBootstrap interrupted by user.", file=sys.stderr)
        log.warning(
            "Bootstrap interrupted by KeyboardInterrupt",
            run_id=run_id,
            elapsed_sec=round(elapsed, 2),
        )
        finish_run(run_id, status="partial", duration_sec=elapsed)
        sys.exit(1)

    elapsed = time.monotonic() - wall_start

    # --- PHASE 2: FEATURE COMPUTATION ---
    # Runs only when OHLCV bootstrap succeeded for at least one symbol
    # and the --skip-features flag was NOT set.
    if not args.skip_features:
        from features.feature_store import (
            bootstrap as bootstrap_features,
            needs_bootstrap,
        )

        # Reuse app_config already loaded above — no second file read needed.
        _app_config: dict = app_config

        feat_start = time.monotonic()
        feat_success = 0
        feat_skipped = 0

        for symbol in symbols_to_bootstrap:
            if needs_bootstrap(symbol, _app_config):
                try:
                    bootstrap_features(symbol, _app_config)
                    feat_success += 1
                    log.debug("Feature bootstrap done", symbol=symbol)
                except Exception as _exc:
                    feat_skipped += 1
                    log.warning(
                        "Feature bootstrap failed — skipping",
                        symbol=symbol,
                        reason=str(_exc),
                    )
            else:
                feat_skipped += 1
                log.debug(
                    "Feature bootstrap skipped — already present",
                    symbol=symbol,
                )

        feat_elapsed = time.monotonic() - feat_start
        feat_msg = (
            f"Feature bootstrap complete: {feat_success} symbols, "
            f"{feat_skipped} skipped (insufficient data or already present)"
        )
        print(feat_msg)
        log.info(
            "Feature bootstrap complete",
            n_success=feat_success,
            n_skipped=feat_skipped,
            duration_sec=round(feat_elapsed, 2),
        )
    # --- END PHASE 2 ---

    # ── Symbol metadata (Gap 5 fix) ─────────────────────────────────────────
    from ingestion.universe_loader import generate_symbol_metadata
    meta_path = Path("data/metadata/symbol_info.csv")
    if not meta_path.exists() or args.force:
        log.info("Generating symbol metadata (sector/industry/mktcap)...")
        generate_symbol_metadata(symbols_to_bootstrap, meta_path)
        log.info("Symbol metadata written", path=str(meta_path))
    else:
        log.info("Symbol metadata already exists, skipping", path=str(meta_path))

    # ── Step 10: Final summary ────────────────────────────────────────────────
    n_ok      = sum(1 for r in results if r and r.get("status") == "ok")
    n_skipped = sum(1 for r in results if r and r.get("status") == "skipped")
    n_failed  = sum(1 for r in results if not r or r.get("status") == "failed")

    print()
    print(
        f"Bootstrap complete │ downloaded: {n_ok} │ skipped: {n_skipped} │ "
        f"failed: {n_failed} │ elapsed: {elapsed:.2f}s"
    )

    # ── Step 11: Mark run complete ────────────────────────────────────────────
    # "partial" if some symbols failed but at least one succeeded (ok or skipped).
    # "failed"  if zero symbols succeeded (all failed or no data at all).
    n_succeeded = n_ok + n_skipped
    if n_succeeded == 0:
        final_status = "failed"
    elif n_failed > 0:
        final_status = "partial"
    else:
        final_status = "success"

    finish_run(
        run_id=run_id,
        status=final_status,
        duration_sec=elapsed,
    )

    log.info(
        "Bootstrap finished",
        run_id=run_id,
        downloaded=n_ok,
        skipped=n_skipped,
        failed=n_failed,
        duration_sec=round(elapsed, 2),
        final_status=final_status,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level guard
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
