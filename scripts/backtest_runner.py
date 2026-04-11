"""
scripts/backtest_runner.py
──────────────────────────
CLI entry point for running Minervini AI backtests (Phase 8).

Usage examples
──────────────
  # Full walk-forward backtest with 7% trailing stop
  python scripts/backtest_runner.py \\
      --start 2019-01-01 --end 2024-01-01 \\
      --universe nifty500 --trailing-stop 0.07

  # Parameter sweep across [5%, 7%, 10%, 15%, fixed]
  python scripts/backtest_runner.py \\
      --start 2022-01-01 --end 2024-01-01 \\
      --sweep --output-dir reports/backtest/

  # Fixed stop only (no trailing stop)
  python scripts/backtest_runner.py \\
      --start 2019-01-01 --end 2024-01-01 --no-trailing
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# ── ensure project root is on sys.path when run directly ──────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── constants ─────────────────────────────────────────────────────────────────
DEFAULT_CONFIG_PATH:    str = "config/settings.yaml"
DEFAULT_DB_PATH:        str = "data/minervini.db"
DEFAULT_OUTPUT_DIR:     str = "reports/backtest/"
DEFAULT_BENCHMARK_PATH: str = "data/features/NIFTY500.parquet"
SEPARATOR_WIDTH:        int = 53
SWEEP_STOP_VALUES:      list = [0.05, 0.07, 0.10, 0.15, None]

# ── project imports ────────────────────────────────────────────────────────────
from backtest.engine import run_backtest, run_parameter_sweep
from backtest.report import generate_report
from utils.exceptions import BacktestDataError, MinerviniError
from utils.logger import get_logger, setup_logging

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtest_runner.py",
        description="Minervini AI — walk-forward backtest CLI (Phase 8).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--start",
        required=True,
        metavar="YYYY-MM-DD",
        help="Backtest start date (inclusive), e.g. 2019-01-01.",
    )
    parser.add_argument(
        "--end",
        required=True,
        metavar="YYYY-MM-DD",
        help="Backtest end date (inclusive), e.g. 2024-01-01.",
    )
    parser.add_argument(
        "--trailing-stop",
        dest="trailing_stop",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Trailing stop fraction, e.g. 0.07 for 7%%. Overrides config value.",
    )
    parser.add_argument(
        "--no-trailing",
        dest="no_trailing",
        action="store_true",
        default=False,
        help="Disable trailing stop; use fixed stop only.",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        default=False,
        help="Run a parameter sweep across trailing stop values [5%%, 7%%, 10%%, 15%%, fixed].",
    )
    parser.add_argument(
        "--universe",
        default="nifty500",
        metavar="NAME",
        help="Universe identifier (informational; screener DB already contains filtered symbols).",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=DEFAULT_OUTPUT_DIR,
        metavar="PATH",
        help=f"Directory for report outputs (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--db-path",
        dest="db_path",
        default=DEFAULT_DB_PATH,
        metavar="PATH",
        help=f"SQLite database path (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        default=DEFAULT_CONFIG_PATH,
        metavar="PATH",
        help=f"settings.yaml path (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--label",
        default="",
        metavar="STR",
        help="Label appended to output filenames, e.g. 'sweep_2019_2024'.",
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(raw: str, flag: str) -> date:
    """
    Parse an ISO date string into a date object.

    Raises:
        SystemExit(1): if the string is not a valid YYYY-MM-DD date.
    """
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        print(
            f"ERROR: {flag} '{raw}' is not a valid ISO date (YYYY-MM-DD).",
            file=sys.stderr,
        )
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_benchmark(path: str):
    """
    Attempt to load a benchmark OHLCV Parquet file.
    Returns a DataFrame on success, or None if the file is absent.
    Never raises — missing benchmark is non-fatal.
    """
    import pandas as pd

    p = Path(path)
    if not p.exists():
        log.debug("Benchmark file not found, skipping regime labelling", path=path)
        return None
    try:
        df = pd.read_parquet(p)
        log.info("Benchmark loaded", path=path, rows=len(df))
        return df
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load benchmark file", path=path, error=str(exc))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Summary printer
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(
    start_date: date,
    end_date: date,
    metrics,
    html_path: Path,
) -> None:
    """Print a concise backtest summary table to stdout."""
    rule = "─" * SEPARATOR_WIDTH
    print()
    print(rule)
    print(f"Backtest Complete: {start_date} → {end_date}")
    print(rule)
    print(f"  {'CAGR':<22}: {metrics.cagr:.1f}%")
    print(f"  {'Sharpe':<22}: {metrics.sharpe_ratio:.2f}")
    print(f"  {'Max Drawdown':<22}: {metrics.max_drawdown_pct:.1f}%")
    print(f"  {'Win Rate':<22}: {metrics.win_rate:.1f}%")
    print(f"  {'Total Trades':<22}: {metrics.total_trades}")
    print(f"  {'Report':<22}: {html_path}")
    print(rule)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Backtest CLI entry point.

    Flow:
        1.  Parse CLI arguments and set up logging.
        2.  Parse + validate --start / --end dates.
        3.  Load config/settings.yaml; inject trailing_stop_pct override.
        4.  Load benchmark OHLCV (NIFTY500.parquet) — skip gracefully if absent.
        5a. --sweep  → run_parameter_sweep() across SWEEP_STOP_VALUES.
        5b. default  → run_backtest() with the resolved trailing_stop_pct.
        6.  generate_report() → HTML + CSV + equity chart PNG.
        7.  Print summary table to stdout.

    Exit codes:
        0 — success
        1 — any MinerviniError or unexpected exception
    """
    setup_logging()
    parser = _build_parser()
    args = parser.parse_args()

    # ── Step 1: parse dates ───────────────────────────────────────────────────
    start_date = _parse_date(args.start, "--start")
    end_date   = _parse_date(args.end,   "--end")

    if start_date >= end_date:
        raise BacktestDataError(
            start_date=str(start_date),
            end_date=str(end_date),
            reason="--start must be strictly before --end",
        )

    # ── Step 2: load config ───────────────────────────────────────────────────
    import yaml

    config_path = Path(args.config_path)
    try:
        with open(config_path, encoding="utf-8") as fh:
            config: dict = yaml.safe_load(fh) or {}
    except (OSError, Exception) as exc:
        print(f"ERROR: Could not load config from {config_path}: {exc}", file=sys.stderr)
        log.error("Config load failed", path=str(config_path), reason=str(exc))
        sys.exit(1)

    # ── Step 3: apply trailing_stop_pct override ──────────────────────────────
    bt_section = config.setdefault("backtest", {})

    if args.no_trailing:
        bt_section["trailing_stop_pct"] = None
        log.info("--no-trailing set; trailing stop disabled")
    elif args.trailing_stop is not None:
        bt_section["trailing_stop_pct"] = float(args.trailing_stop)
        log.info("trailing_stop_pct overridden", value=args.trailing_stop)

    # ── Step 4: load benchmark ────────────────────────────────────────────────
    benchmark_df = _load_benchmark(DEFAULT_BENCHMARK_PATH)

    db_path    = Path(args.db_path)
    output_dir = Path(args.output_dir)
    label      = args.label or "run"

    log.info(
        "backtest_runner starting",
        start=str(start_date),
        end=str(end_date),
        sweep=args.sweep,
        db_path=str(db_path),
    )

    try:
        # ── Step 5a: parameter sweep ──────────────────────────────────────────
        if args.sweep:
            sweep_results = run_parameter_sweep(
                start_date=start_date,
                end_date=end_date,
                config=config,
                db_path=db_path,
                trailing_stop_values=SWEEP_STOP_VALUES,
            )
            # Run a single baseline backtest for the report (uses config value)
            result = run_backtest(
                start_date=start_date,
                end_date=end_date,
                config=config,
                db_path=db_path,
                benchmark_df=benchmark_df,
            )
            # Attach sweep summary to result for inclusion in the HTML report
            from dataclasses import replace as _dc_replace
            result = _dc_replace(result, parameter_sweep=sweep_results)

        # ── Step 5b: single backtest ──────────────────────────────────────────
        else:
            result = run_backtest(
                start_date=start_date,
                end_date=end_date,
                config=config,
                db_path=db_path,
                benchmark_df=benchmark_df,
            )

        # ── Step 6: generate report ───────────────────────────────────────────
        report_paths = generate_report(result, output_dir, run_label=label)

        # ── Step 7: print summary ─────────────────────────────────────────────
        _print_summary(
            start_date=start_date,
            end_date=end_date,
            metrics=result.metrics,
            html_path=report_paths["html"],
        )

    except BacktestDataError as exc:
        print(f"ERROR [BacktestDataError]: {exc}", file=sys.stderr)
        log.error("Backtest data error", reason=str(exc))
        sys.exit(1)
    except MinerviniError as exc:
        print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        log.error("Minervini domain error", reason=str(exc))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Unexpected error: {exc}", file=sys.stderr)
        log.exception("Unexpected error in backtest_runner")
        sys.exit(1)

    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level guard
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
