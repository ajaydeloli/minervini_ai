"""
backtest/engine.py
──────────────────
Walk-forward backtester for the Minervini AI system (Phase 8).

No-lookahead-bias design
────────────────────────
For each trading date D in [start_date, end_date]:

  1. load_screen_results(db_path, D)       — screener candidates for D
  2. load_prices_for_date(D, symbols, cfg) — today's close prices
  3. portfolio.update_trailing_stops(prev_prices, trailing_stop_pct)
     ↑ uses YESTERDAY'S close (prev_prices), never today's close
  4. portfolio.check_exits(prev_prices, D, max_hold_days)
     ↑ exits at yesterday's close ≈ today's open  →  zero lookahead
  5. Enter new positions from candidates at today's close (today_prices)
  6. Advance: prev_prices = today_prices

After the loop:
  • portfolio.close_all(last_prices, end_date)
  • label_trades() → add regime labels
  • compute_regime_breakdown() → per-regime stats
  • compute_metrics()          → BacktestMetrics
  • build equity_curve from trade list

Public API
──────────
    BacktestResult           — frozen output dataclass
    run_backtest(...)        → BacktestResult
    load_screen_results(...) → list[dict]
    load_prices_for_date(...)→ dict[str, float]
    run_parameter_sweep(...) → list[dict]
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from backtest.metrics import BacktestMetrics, compute_equity_curve, compute_metrics
from backtest.portfolio import BacktestPortfolio
from backtest.regime import compute_regime_breakdown, label_trades
from screener.results import load_results
from utils.exceptions import BacktestError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_INITIAL_CAPITAL: float = 100_000.0
_DEFAULT_TRAILING_STOP:   float = 0.07
_DEFAULT_MAX_HOLD:        int   = 20
_DEFAULT_FIXED_STOP:      float = 0.05
_DEFAULT_SWEEP_VALUES: list     = [0.05, 0.07, 0.10, 0.15, None]


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    """
    Immutable output of one complete walk-forward backtest run.

    Fields
    ──────
    start_date       : Inclusive start of the backtest window.
    end_date         : Inclusive end of the backtest window.
    initial_capital  : Starting portfolio value in currency units.
    final_capital    : Ending portfolio value after all positions are closed.
    config_snapshot  : Deep copy of the config dict used for reproducibility.
    metrics          : BacktestMetrics (CAGR, Sharpe, drawdown, …).
    trades           : All closed trade dicts produced by portfolio.to_trade_list().
    equity_curve     : pd.DataFrame with DatetimeIndex, columns equity & daily_return_pct.
    regime_breakdown : Per-regime performance dict from compute_regime_breakdown().
    gate_stats       : Dict tracking how many dates / candidates passed Stage2 & TT gates.
    parameter_sweep  : Populated only by run_parameter_sweep(); None otherwise.
    """

    start_date:        date
    end_date:          date
    initial_capital:   float
    final_capital:     float
    config_snapshot:   dict
    metrics:           BacktestMetrics
    trades:            list[dict]
    equity_curve:      pd.DataFrame
    regime_breakdown:  dict[str, dict]
    gate_stats:        dict
    parameter_sweep:   Optional[list[dict]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_screen_results(db_path: Path, run_date: date) -> list[dict]:
    """
    Load SEPA screener results for *run_date* from the SQLite sepa_results table.

    Uses screener/results.py ``load_results()`` under the hood.
    Returns an empty list if the database file does not exist, the table is
    absent, or no rows exist for that date.

    Parameters
    ──────────
    db_path  : Path to the SQLite database file.
    run_date : The trading date whose results to fetch.

    Returns
    ───────
    list[dict] sorted by score descending (order preserved from load_results).
    """
    date_str = run_date.isoformat()
    try:
        results = load_results(db_path, run_date=date_str)
    except Exception as exc:   # noqa: BLE001
        log.warning(
            "load_screen_results failed — returning []",
            date=date_str,
            error=str(exc),
        )
        return []

    log.debug(
        "load_screen_results",
        date=date_str,
        count=len(results),
    )
    return results


def load_prices_for_date(
    target_date: date,
    symbols: list[str],
    config: dict,
) -> dict[str, float]:
    """
    Load the closing price for each symbol on *target_date*.

    Reads Parquet files at ``{features_dir}/{symbol}.parquet``.
    The Parquet files are expected to have a DatetimeIndex and a 'close' column.

    Parameters
    ──────────
    target_date  : The date for which we want closing prices.
    symbols      : Symbols to fetch.
    config       : Full application config dict; reads data.features_dir.

    Returns
    ───────
    dict[symbol, close_price] — only symbols with a matching date row are included.
    Symbols with missing files or no row for target_date are silently omitted.
    """
    features_dir = Path(config.get("data", {}).get("features_dir", "data/features"))
    ts = pd.Timestamp(target_date)
    prices: dict[str, float] = {}

    for symbol in symbols:
        parquet_path = features_dir / f"{symbol}.parquet"
        if not parquet_path.exists():
            log.debug("load_prices_for_date: file not found", symbol=symbol)
            continue
        try:
            df = pd.read_parquet(parquet_path, columns=["close"])
            if df.index.dtype != "datetime64[ns]":
                df.index = pd.to_datetime(df.index)
            row = df[df.index == ts]
            if row.empty:
                continue
            prices[symbol] = float(row["close"].iloc[0])
        except Exception as exc:   # noqa: BLE001
            log.warning(
                "load_prices_for_date: read error",
                symbol=symbol,
                date=str(target_date),
                error=str(exc),
            )
    return prices


# ─────────────────────────────────────────────────────────────────────────────
# Core walk-forward engine
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    start_date: date,
    end_date: date,
    config: dict,
    db_path: Path,
    benchmark_df: Optional[pd.DataFrame] = None,
) -> BacktestResult:
    """
    Walk-forward backtest over [start_date, end_date].

    Steps per trading date (no lookahead bias):
      1. load_screen_results(db_path, date)
      2. load_prices_for_date(date, symbols, config)   — today's close
      3. portfolio.update_trailing_stops(prev_prices, trailing_stop_pct)
         ↑ yesterday's close — stop updated BEFORE exit check
      4. portfolio.check_exits(prev_prices, date, max_hold_days)
         ↑ exit at yesterday's close (≈ today's open)
      5. Enter candidates sorted by score desc at today's close
      6. prev_prices ← today_prices

    Parameters
    ──────────
    start_date   : First trading date (inclusive).
    end_date     : Last trading date (inclusive).
    config       : Full application config dict.
    db_path      : Path to SQLite database containing sepa_results.
    benchmark_df : Optional OHLCV DataFrame for regime slope fallback.

    Returns
    ───────
    BacktestResult
    """
    bt_cfg = config.get("backtest", {})
    pt_cfg = config.get("paper_trading", {})

    initial_capital:   float         = float(pt_cfg.get("initial_capital", _DEFAULT_INITIAL_CAPITAL))
    trailing_stop_pct: Optional[float] = bt_cfg.get("trailing_stop_pct", _DEFAULT_TRAILING_STOP)
    max_hold_days:     int           = int(bt_cfg.get("max_hold_days", _DEFAULT_MAX_HOLD))

    # trailing_stop_pct=None means fixed-stop-only mode
    if trailing_stop_pct is not None:
        trailing_stop_pct = float(trailing_stop_pct)

    portfolio = BacktestPortfolio(initial_capital, config)

    # All business days in the window
    trading_dates: list[date] = [
        ts.date()
        for ts in pd.bdate_range(start=start_date, end=end_date)
    ]

    if not trading_dates:
        log.warning("run_backtest: no trading dates in range", start=str(start_date), end=str(end_date))

    gate_stats: dict = {
        "total_candidates": 0,
        "stage2_pass":      0,
        "tt_pass":          0,
        "both_pass":        0,
        "dates_with_results": 0,
    }

    prev_prices: dict[str, float] = {}   # yesterday's close — used for exits

    log.info(
        "run_backtest started",
        start=str(start_date),
        end=str(end_date),
        trading_days=len(trading_dates),
        initial_capital=initial_capital,
        trailing_stop_pct=trailing_stop_pct,
        max_hold_days=max_hold_days,
    )


    for current_date in trading_dates:

        # ── Step 1: load screener results for today ───────────────────────
        candidates: list[dict] = load_screen_results(db_path, current_date)
        if candidates:
            gate_stats["dates_with_results"] += 1

        # ── Step 2: collect all symbols we need prices for ────────────────
        open_symbols  = [p.symbol for p in portfolio.state.open_positions]
        cand_symbols  = [c["symbol"] for c in candidates]
        all_symbols   = list(set(open_symbols + cand_symbols))

        today_prices  = load_prices_for_date(current_date, all_symbols, config)

        # Fill gaps in today's prices with last known price (carry-forward)
        for sym in all_symbols:
            if sym not in today_prices and sym in prev_prices:
                today_prices[sym] = prev_prices[sym]

        # ── Step 3: update trailing stops using YESTERDAY's close ─────────
        # prev_prices = yesterday's close; use it before looking at today
        portfolio.update_trailing_stops(prev_prices, trailing_stop_pct)

        # ── Step 4: check exits at yesterday's close (≈ today's open) ─────
        portfolio.check_exits(prev_prices, current_date, max_hold_days)

        # ── Step 5: enter new positions from today's candidates ───────────
        # Candidates already sorted by score DESC from load_results
        for cand in candidates:
            # Gate stats tracking
            is_stage2 = int(cand.get("stage", 0)) == 2
            is_tt     = bool(cand.get("trend_template_pass", False))

            gate_stats["total_candidates"] += 1
            if is_stage2:
                gate_stats["stage2_pass"] += 1
            if is_tt:
                gate_stats["tt_pass"] += 1
            if is_stage2 and is_tt:
                gate_stats["both_pass"] += 1

            # Only trade quality setups that have an entry price and stop
            entry_price = cand.get("entry_price")
            stop_loss   = cand.get("stop_loss")
            if entry_price is None or stop_loss is None:
                continue
            entry_price = float(entry_price)
            stop_loss   = float(stop_loss)

            symbol = cand["symbol"]
            # Use today's actual close price (if available) as entry
            actual_close = today_prices.get(symbol, entry_price)

            if not portfolio.can_enter(actual_close, stop_loss):
                continue

            portfolio.enter(
                symbol=symbol,
                entry_date=current_date,
                entry_price=actual_close,
                stop_loss=stop_loss,
                target_price=cand.get("target_price"),
                setup_quality=str(cand.get("setup_quality", "C")),
                score=int(cand.get("score", 0)),
                regime=None,   # labelled post-loop
            )

        # ── Advance: today's prices become yesterday's for next iteration ─
        prev_prices = dict(today_prices)


    # ── Post-loop: close remaining open positions at last known prices ────
    last_prices = prev_prices
    portfolio.close_all(last_prices, end_date)

    # ── Build trade list ──────────────────────────────────────────────────
    trades = portfolio.to_trade_list()

    # ── Regime labelling ─────────────────────────────────────────────────
    trades = label_trades(trades, benchmark_df)
    regime_breakdown = compute_regime_breakdown(trades)

    # ── Metrics ───────────────────────────────────────────────────────────
    metrics = compute_metrics(trades, initial_capital, by_regime=regime_breakdown)

    # ── Equity curve ──────────────────────────────────────────────────────
    equity_curve = compute_equity_curve(trades, initial_capital)

    # ── Final capital ─────────────────────────────────────────────────────
    final_capital = portfolio.state.cash + sum(
        p.current_stop * p.qty for p in portfolio.state.open_positions
    )
    # All positions are closed after close_all — cash reflects full proceeds
    final_capital = portfolio.state.cash

    log.info(
        "run_backtest complete",
        trades=len(trades),
        final_capital=round(final_capital, 2),
        cagr=metrics.cagr,
        sharpe=metrics.sharpe_ratio,
        max_drawdown=metrics.max_drawdown_pct,
    )

    return BacktestResult(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        final_capital=round(final_capital, 2),
        config_snapshot=copy.deepcopy(config),
        metrics=metrics,
        trades=trades,
        equity_curve=equity_curve,
        regime_breakdown=regime_breakdown,
        gate_stats=gate_stats,
        parameter_sweep=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parameter sweep
# ─────────────────────────────────────────────────────────────────────────────

def run_parameter_sweep(
    start_date: date,
    end_date: date,
    config: dict,
    db_path: Path,
    trailing_stop_values: Optional[list] = None,
) -> list[dict]:
    """
    Run an independent backtest for each trailing_stop_pct value and return
    a summary list for easy comparison.

    Parameters
    ──────────
    start_date           : Inclusive start date.
    end_date             : Inclusive end date.
    config               : Base config dict (each run gets a deep copy).
    db_path              : Path to SQLite database.
    trailing_stop_values : List of trailing_stop_pct values to sweep.
                           ``None`` in the list means fixed-stop-only mode.
                           Defaults to [0.05, 0.07, 0.10, 0.15, None].

    Returns
    ───────
    list[dict], one per trailing_stop value:
        {
            trailing_stop_pct: float | None,
            cagr:              float,
            sharpe:            float,
            max_drawdown:      float,
            win_rate:          float,
            total_trades:      int,
        }
    """
    if trailing_stop_values is None:
        trailing_stop_values = list(_DEFAULT_SWEEP_VALUES)

    summary: list[dict] = []

    for ts_pct in trailing_stop_values:
        sweep_config = copy.deepcopy(config)
        bt_section   = sweep_config.setdefault("backtest", {})

        if ts_pct is None:
            # Fixed-stop-only: remove trailing_stop_pct from config
            bt_section.pop("trailing_stop_pct", None)
        else:
            bt_section["trailing_stop_pct"] = float(ts_pct)

        log.info("run_parameter_sweep: running", trailing_stop_pct=ts_pct)

        result = run_backtest(
            start_date=start_date,
            end_date=end_date,
            config=sweep_config,
            db_path=db_path,
            benchmark_df=None,
        )

        summary.append({
            "trailing_stop_pct": ts_pct,
            "cagr":              result.metrics.cagr,
            "sharpe":            result.metrics.sharpe_ratio,
            "max_drawdown":      result.metrics.max_drawdown_pct,
            "win_rate":          result.metrics.win_rate,
            "total_trades":      result.metrics.total_trades,
        })

    log.info(
        "run_parameter_sweep complete",
        runs=len(summary),
        values=trailing_stop_values,
    )
    return summary
