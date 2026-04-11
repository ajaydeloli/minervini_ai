"""
scripts/rebuild_features.py
────────────────────────────
Recompute feature Parquet files from existing processed OHLCV data.

Does NOT download any new price data — assumes data/processed/{symbol}.parquet
already exists.  Deletes the stale feature file and calls
features.feature_store.bootstrap(symbol, config) for each resolved symbol.

Usage examples
──────────────
  # Rebuild features for the full nifty500 universe
  python scripts/rebuild_features.py --universe nifty500

  # Rebuild specific symbols
  python scripts/rebuild_features.py --symbols RELIANCE,TCS,DIXON

  # Dry-run: see which symbols would be rebuilt
  python scripts/rebuild_features.py --universe nifty500 --dry-run

  # Only rebuild symbols whose feature file is older than 2024-01-01
  python scripts/rebuild_features.py --universe nifty500 --since 2024-01-01

  # 8 parallel workers
  python scripts/rebuild_features.py --universe nifty500 --workers 8
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Any

import yaml

# ─────────────────────────────────────────────────────────────────────────────
# Ensure the project root is on sys.path when the script is run directly,
# e.g.  python scripts/rebuild_features.py  (without pip install -e .)
# ─────────────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Named constants
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH: str = "config/settings.yaml"
DEFAULT_WORKERS: int = 4
SEPARATOR_WIDTH: int = 55
BANNER_TITLE: str = "Minervini — Rebuild Features"

# ─────────────────────────────────────────────────────────────────────────────
# Project imports — deferred to after sys.path is safe
# ─────────────────────────────────────────────────────────────────────────────

from ingestion.universe_loader import RunSymbols, resolve_symbols
from utils.exceptions import InvalidSymbolError, UniverseLoadError, WatchlistParseError
from utils.logger import get_logger, setup_logging

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rebuild_features.py",
        description="Minervini AI — recompute feature Parquet files from existing processed data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--universe",
        metavar="KEY",
        help='Universe key from config (e.g. "nifty500"). Mutually exclusive with --symbols.',
    )
    source.add_argument(
        "--symbols",
        metavar="SYM1,SYM2",
        help="Comma-separated symbol list. Mutually exclusive with --universe.",
    )

    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        default=None,
        help=(
            "Only rebuild symbols whose feature file is older than this date "
            "(or missing entirely). Symbols with a newer feature file are skipped."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print which symbols would be rebuilt without actually running bootstrap().",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        metavar="N",
        help=f"Number of parallel processes (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        metavar="PATH",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to settings.yaml (default: {DEFAULT_CONFIG_PATH}).",
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# --since filter helper
# ─────────────────────────────────────────────────────────────────────────────

def _feature_is_stale(feature_path: Path, since: date) -> bool:
    """
    Return True when the feature file should be rebuilt.

    Rules:
      - File missing               → stale (needs rebuild)
      - File mtime < since date    → stale (needs rebuild)
      - File mtime >= since date   → fresh (skip)
    """
    if not feature_path.exists():
        return True
    mtime = datetime.fromtimestamp(feature_path.stat().st_mtime).date()
    return mtime < since


# ─────────────────────────────────────────────────────────────────────────────
# Per-symbol worker — runs inside a child process
# ─────────────────────────────────────────────────────────────────────────────

def _rebuild_symbol(symbol: str, config: dict[str, Any]) -> dict[str, Any]:
    """
    Delete the existing feature file and recompute it via feature_store.bootstrap.

    This function runs inside a ProcessPoolExecutor child process.  It must
    be a top-level function (not a closure) so pickle can serialise it.

    Returns:
        dict with keys: status ("ok" | "failed"), symbol, reason (str).
    """
    # Import inside the worker so the child process initialises its own state.
    import logging
    import os
    from features.feature_store import bootstrap as bootstrap_features
    from utils.logger import get_logger as _get_logger, setup_logging as _setup_logging

    # Minimal logging setup in child process (stderr only — no file contention).
    _setup_logging()
    _log = _get_logger(__name__)

    feature_dir = Path(config["data"]["features_dir"])
    feature_path = feature_dir / f"{symbol}.parquet"

    # Delete stale file so bootstrap writes a clean copy.
    if feature_path.exists():
        try:
            feature_path.unlink()
            _log.debug("Deleted stale feature file", symbol=symbol, path=str(feature_path))
        except OSError as exc:
            return {"status": "failed", "symbol": symbol, "reason": f"delete error: {exc}"}

    # Recompute.
    try:
        bootstrap_features(symbol, config)
    except Exception as exc:
        return {"status": "failed", "symbol": symbol, "reason": str(exc)}

    _log.info("Feature rebuild complete", symbol=symbol)
    return {"status": "ok", "symbol": symbol, "reason": ""}


# ─────────────────────────────────────────────────────────────────────────────
# Summary printer
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner(symbols: list[str], since: date | None, workers: int, dry_run: bool) -> None:
    rule = "─" * SEPARATOR_WIDTH
    title_rule = f"── {BANNER_TITLE} " + "─" * (SEPARATOR_WIDTH - len(BANNER_TITLE) - 4)
    print(title_rule)
    print(f"{'Symbols':<14}: {len(symbols)}")
    print(f"{'Since filter':<14}: {since.isoformat() if since else '(none — rebuild all)'}")
    print(f"{'Workers':<14}: {workers}")
    print(f"{'Dry run':<14}: {dry_run}")
    print(rule)


def _print_symbol_list(symbols: list[str], label: str = "Symbols to rebuild") -> None:
    print(f"\n{label} ({len(symbols)}):")
    for sym in symbols:
        print(f"  {sym}")
    print()


def _print_summary(
    rebuilt: list[str],
    skipped: list[str],
    failed: list[str],
    elapsed: float,
    dry_run: bool,
) -> None:
    print()
    dry_tag = " (dry-run)" if dry_run else ""
    print(
        f"Rebuild complete{dry_tag} │ "
        f"rebuilt: {len(rebuilt)} │ "
        f"skipped: {len(skipped)} │ "
        f"failed: {len(failed)} │ "
        f"elapsed: {elapsed:.2f}s"
    )
    if failed:
        print(f"  Failed symbols: {', '.join(failed)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Rebuild features entry point.

    Flow:
        1. Parse CLI arguments.
        2. Set up logging.
        3. Load config from --config path.
        4. Resolve symbols via --universe or --symbols.
        5. Apply --since filter to determine which symbols need rebuilding.
        6. Print banner.
        7. If --dry-run: print symbol list and exit.
        8. Rebuild each symbol in parallel with ProcessPoolExecutor.
        9. Print summary (rebuilt / skipped / failed).

    Exit codes:
        0 — success or dry-run
        1 — fatal error (bad args, missing config, empty symbol list)
    """
    setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    log.info("Rebuild features starting", config=args.config_path)

    # ── Load config ──────────────────────────────────────────────────────────
    config_path = Path(args.config_path)
    if not config_path.exists():
        print(f"ERROR: Config file not found: '{config_path}'", file=sys.stderr)
        log.error("Config file not found", path=str(config_path))
        sys.exit(1)

    try:
        with config_path.open(encoding="utf-8") as fh:
            config: dict[str, Any] = yaml.safe_load(fh) or {}
    except Exception as exc:
        print(f"ERROR: Cannot load config '{config_path}': {exc}", file=sys.stderr)
        log.error("Config load failed", path=str(config_path), reason=str(exc))
        sys.exit(1)

    if not config:
        print(f"ERROR: Config file is empty: '{config_path}'", file=sys.stderr)
        sys.exit(1)


    # ── Parse --since ────────────────────────────────────────────────────────
    since: date | None = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").date()
        except ValueError:
            print(
                f"ERROR: --since must be YYYY-MM-DD, got '{args.since}'",
                file=sys.stderr,
            )
            sys.exit(1)

    # ── Resolve symbols ──────────────────────────────────────────────────────
    cli_symbols: list[str] | None = None
    scope = "universe"

    if args.symbols:
        cli_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.universe:
        # Map universe key to resolve_symbols scope
        scope = "universe"

    try:
        run_symbols: RunSymbols = resolve_symbols(
            config_path="config/universe.yaml",
            cli_watchlist_file=None,
            cli_symbols=cli_symbols,
            scope=scope,  # type: ignore[arg-type]
        )
    except WatchlistParseError as exc:
        print(f"ERROR [WatchlistParseError]: {exc}", file=sys.stderr)
        sys.exit(1)
    except UniverseLoadError as exc:
        print(f"ERROR [UniverseLoadError]: {exc}", file=sys.stderr)
        sys.exit(1)
    except InvalidSymbolError as exc:
        print(f"ERROR [InvalidSymbolError]: {exc}", file=sys.stderr)
        sys.exit(1)

    all_symbols: list[str] = run_symbols.symbols_to_scan

    if not all_symbols:
        print(
            "ERROR: No symbols resolved. Check config/universe.yaml or use --symbols.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Apply --since filter ─────────────────────────────────────────────────
    feature_dir = Path(config["data"]["features_dir"])

    if since is not None:
        symbols_to_rebuild = [
            sym for sym in all_symbols
            if _feature_is_stale(feature_dir / f"{sym}.parquet", since)
        ]
        symbols_skipped_by_since = [s for s in all_symbols if s not in symbols_to_rebuild]
    else:
        symbols_to_rebuild = list(all_symbols)
        symbols_skipped_by_since = []

    log.info(
        "Symbol filter applied",
        total=len(all_symbols),
        to_rebuild=len(symbols_to_rebuild),
        skipped_by_since=len(symbols_skipped_by_since),
    )

    # ── Banner ───────────────────────────────────────────────────────────────
    print()
    _print_banner(symbols_to_rebuild, since, args.workers, args.dry_run)
    print(f"Rebuilding {len(symbols_to_rebuild)} symbols...")

    # ── Dry-run fast exit ────────────────────────────────────────────────────
    if args.dry_run:
        _print_symbol_list(symbols_to_rebuild, label="Symbols that would be rebuilt")
        if symbols_skipped_by_since:
            _print_symbol_list(
                symbols_skipped_by_since,
                label=f"Symbols skipped (feature file newer than {since})",
            )
        _print_summary(
            rebuilt=[],
            skipped=symbols_to_rebuild,   # all are "would-be skipped" in dry-run terms
            failed=[],
            elapsed=0.0,
            dry_run=True,
        )
        log.info("Dry run complete — no files modified")
        sys.exit(0)


    # ── Parallel rebuild ─────────────────────────────────────────────────────
    wall_start = time.monotonic()
    total = len(symbols_to_rebuild)

    rebuilt: list[str] = []
    failed_symbols: list[str] = []
    results: list[dict] = []

    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            future_to_sym = {
                executor.submit(_rebuild_symbol, sym, config): sym
                for sym in symbols_to_rebuild
            }

            for idx, future in enumerate(as_completed(future_to_sym), start=1):
                sym = future_to_sym[future]
                try:
                    result = future.result()
                except Exception as exc:
                    log.warning(
                        "Unexpected worker error",
                        symbol=sym,
                        reason=str(exc),
                    )
                    result = {"status": "failed", "symbol": sym, "reason": str(exc)}

                results.append(result)

                if result["status"] == "ok":
                    rebuilt.append(sym)
                    log.info("Symbol rebuilt", symbol=sym, progress=f"{idx}/{total}")
                else:
                    failed_symbols.append(sym)
                    log.warning(
                        "Symbol rebuild failed",
                        symbol=sym,
                        reason=result.get("reason", "unknown"),
                    )

    except KeyboardInterrupt:
        elapsed = time.monotonic() - wall_start
        print("\nRebuild interrupted by user.", file=sys.stderr)
        log.warning("Rebuild interrupted", elapsed_sec=round(elapsed, 2))
        sys.exit(1)

    elapsed = time.monotonic() - wall_start

    # ── Final summary ────────────────────────────────────────────────────────
    _print_summary(
        rebuilt=rebuilt,
        skipped=symbols_skipped_by_since,
        failed=failed_symbols,
        elapsed=elapsed,
        dry_run=False,
    )

    log.info(
        "Rebuild features finished",
        rebuilt=len(rebuilt),
        skipped=len(symbols_skipped_by_since),
        failed=len(failed_symbols),
        duration_sec=round(elapsed, 2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level guard
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
