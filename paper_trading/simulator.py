"""
paper_trading/simulator.py
──────────────────────────
Phase 7 — Paper Trading Simulator for the Minervini AI system.

This module is the high-level coordinator between:
    • paper_trading/portfolio.py  — Trade / PortfolioState / DB functions
    • paper_trading/order_queue.py — is_market_open / queue_order / execute_pending_orders
    • rules/scorer.py             — SEPAResult (screener output)

It NEVER touches SQLite directly; all persistence goes through portfolio.py
and order_queue.py.

Public API
──────────
    _get_config_values(config)                              → dict
    _compute_qty(portfolio_value, cash, entry_price,        → int
                 stop_loss, risk_pct)
    enter_trade(result, db_path, config)                    → Trade | None
    pyramid_position(result, db_path, config)               → Trade | None
    check_exits(current_prices, db_path, config)            → list[Trade]
    process_screen_results(results, db_path, config)        → dict
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Optional

from paper_trading.order_queue import is_market_open, queue_order
from paper_trading.portfolio import (
    Trade,
    get_open_positions,
    get_portfolio_state,
    get_position,
    close_position,
    mark_pyramided,
    open_position,
)
from rules.scorer import SEPAResult
from utils.exceptions import PaperTradingError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_config_values(config: dict) -> dict:
    """
    Extract paper_trading section from config with safe defaults.

    Returns a flat dict with keys:
        enabled, initial_capital, max_positions, risk_per_trade_pct,
        min_score_to_trade, min_confidence, expiry_days
    """
    pt = config.get("paper_trading", {})
    return {
        "enabled":            bool(pt.get("enabled",            True)),
        "initial_capital":    float(pt.get("initial_capital",   100_000)),
        "max_positions":      int(pt.get("max_positions",       10)),
        "risk_per_trade_pct": float(pt.get("risk_per_trade_pct", 2.0)),
        "min_score_to_trade": int(pt.get("min_score_to_trade",  70)),
        "min_confidence":     int(pt.get("min_confidence",      50)),
        "expiry_days":        int(pt.get("expiry_days",         2)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Position sizing
# ─────────────────────────────────────────────────────────────────────────────

def _compute_qty(
    portfolio_value: float,
    cash: float,
    entry_price: float,
    stop_loss: float,
    risk_pct: float,
) -> int:
    """
    Position sizing: risk a fixed percentage of portfolio value per trade.

    Formula
    ───────
    risk_amount    = portfolio_value × (risk_pct / 100)
    risk_per_share = entry_price − stop_loss
    qty            = floor(risk_amount / risk_per_share)

    Then clamp to available cash:
        if qty × entry_price > cash:  qty = floor(cash / entry_price)

    Returns 0 if stop_loss >= entry_price or final qty is 0.
    """
    if stop_loss >= entry_price:
        return 0

    risk_per_share = entry_price - stop_loss
    risk_amount    = portfolio_value * (risk_pct / 100.0)

    qty = math.floor(risk_amount / risk_per_share)

    # Clamp to available cash
    if qty * entry_price > cash:
        qty = math.floor(cash / entry_price)

    return max(0, qty)


# ─────────────────────────────────────────────────────────────────────────────
# enter_trade
# ─────────────────────────────────────────────────────────────────────────────

def enter_trade(
    result: SEPAResult,
    db_path: Path,
    config: dict,
) -> Optional[Trade]:
    """
    Attempt to open a new paper position based on a SEPAResult.

    Returns the opened Trade when the market is open, or None when:
      - paper trading is disabled
      - score / quality gates not met
      - max positions already reached
      - symbol already held
      - qty resolves to zero
      - market is closed (order is queued instead)

    Side-effects:
      - Calls open_position() when market is open → deducts cash, inserts row.
      - Calls queue_order() when market is closed → inserts pending order row.
    """
    cfg = _get_config_values(config)

    if not cfg["enabled"]:
        log.debug("enter_trade: paper trading disabled", symbol=result.symbol)
        return None

    if result.score < cfg["min_score_to_trade"]:
        log.debug(
            "enter_trade: score below threshold",
            symbol=result.symbol,
            score=result.score,
            threshold=cfg["min_score_to_trade"],
        )
        return None

    if result.setup_quality not in ("A+", "A"):
        log.debug(
            "enter_trade: setup quality not A/A+",
            symbol=result.symbol,
            quality=result.setup_quality,
        )
        return None

    if result.entry_price is None or result.stop_loss is None:
        log.debug(
            "enter_trade: missing entry_price or stop_loss",
            symbol=result.symbol,
        )
        return None

    # ── Portfolio state checks ────────────────────────────────────────────────
    try:
        state     = get_portfolio_state(db_path)
        positions = get_open_positions(db_path)
    except Exception as exc:
        log.warning(
            "enter_trade: could not load portfolio state",
            symbol=result.symbol,
            error=str(exc),
        )
        return None

    if len(positions) >= cfg["max_positions"]:
        log.debug(
            "enter_trade: max positions reached",
            symbol=result.symbol,
            open_positions=len(positions),
        )
        return None

    try:
        existing = get_position(db_path, result.symbol)
    except Exception as exc:
        log.warning(
            "enter_trade: get_position failed",
            symbol=result.symbol,
            error=str(exc),
        )
        return None

    if existing is not None:
        log.debug("enter_trade: already holding", symbol=result.symbol)
        return None

    # ── Position sizing ───────────────────────────────────────────────────────
    portfolio_value = state.cash + sum(p.entry_price * p.qty for p in positions)
    qty = _compute_qty(
        portfolio_value=portfolio_value,
        cash=state.cash,
        entry_price=result.entry_price,
        stop_loss=result.stop_loss,
        risk_pct=cfg["risk_per_trade_pct"],
    )

    if qty == 0:
        log.debug(
            "enter_trade: qty resolved to 0",
            symbol=result.symbol,
            entry_price=result.entry_price,
            stop_loss=result.stop_loss,
        )
        return None

    trade = Trade(
        symbol=result.symbol,
        entry_date=date.today(),
        entry_price=result.entry_price,
        qty=qty,
        stop_loss=result.stop_loss,
        target_price=result.target_price,
        risk_pct=result.risk_pct,
        rr_ratio=result.rr_ratio,
        setup_quality=result.setup_quality,
        score=result.score,
        pyramided=False,
        status="open",
    )

    # ── Market open → execute immediately; else queue ─────────────────────────
    if is_market_open():
        try:
            opened = open_position(db_path, trade)
            log.info(
                "enter_trade: position opened",
                symbol=result.symbol,
                qty=qty,
                entry_price=result.entry_price,
            )
            return opened
        except Exception as exc:
            log.warning(
                "enter_trade: open_position failed",
                symbol=result.symbol,
                error=str(exc),
            )
            return None
    else:
        try:
            queue_order(
                db_path=db_path,
                symbol=result.symbol,
                order_type="enter",
                entry_price=result.entry_price,
                stop_loss=result.stop_loss,
                target_price=result.target_price,
                risk_pct=result.risk_pct,
                rr_ratio=result.rr_ratio,
                setup_quality=result.setup_quality,
                score=result.score,
                expiry_days=cfg["expiry_days"],
            )
            log.info(
                "enter_trade: order queued (market closed)",
                symbol=result.symbol,
                qty=qty,
                entry_price=result.entry_price,
            )
        except Exception as exc:
            log.warning(
                "enter_trade: queue_order failed",
                symbol=result.symbol,
                error=str(exc),
            )
        return None  # not yet a Trade — pending


# ─────────────────────────────────────────────────────────────────────────────
# pyramid_position
# ─────────────────────────────────────────────────────────────────────────────

def pyramid_position(
    result: SEPAResult,
    db_path: Path,
    config: dict,
) -> Optional[Trade]:
    """
    Add to an existing position (pyramid) if strict VCP-grade conditions hold.

    Add-on size: floor(original_qty × 0.5) shares.

    Returns the new (pyramid) Trade when the market is open, or None when:
      - paper trading disabled
      - symbol not held / already pyramided
      - VCP quality gate fails
      - entry price has drifted > 2 % from original entry
      - add_qty == 0 or insufficient cash
      - market is closed (order is queued)

    Side-effects:
      - Calls open_position() for the add-on Trade (pyramided=True).
      - Calls mark_pyramided() on the original position.
    """
    cfg = _get_config_values(config)

    if not cfg["enabled"]:
        return None

    try:
        position = get_position(db_path, result.symbol)
    except Exception as exc:
        log.warning(
            "pyramid_position: get_position failed",
            symbol=result.symbol,
            error=str(exc),
        )
        return None

    if position is None:
        log.debug("pyramid_position: symbol not held", symbol=result.symbol)
        return None

    if position.pyramided:
        log.debug("pyramid_position: already pyramided", symbol=result.symbol)
        return None

    if not result.vcp_qualified:
        log.debug("pyramid_position: vcp not qualified", symbol=result.symbol)
        return None

    vcp_grade = result.vcp_details.get("quality_grade")
    if vcp_grade != "A":
        log.debug(
            "pyramid_position: vcp grade not A",
            symbol=result.symbol,
            vcp_grade=vcp_grade,
        )
        return None

    vol_contraction = result.vcp_details.get("vol_ratio", 1.0)
    if vol_contraction >= 0.4:
        log.debug(
            "pyramid_position: vol_ratio >= 0.4 (insufficient contraction)",
            symbol=result.symbol,
            vol_ratio=vol_contraction,
        )
        return None

    if result.entry_price is None:
        log.debug("pyramid_position: no entry_price", symbol=result.symbol)
        return None

    price_drift = abs(result.entry_price - position.entry_price) / position.entry_price
    if price_drift > 0.02:
        log.debug(
            "pyramid_position: entry price drifted > 2 %",
            symbol=result.symbol,
            drift_pct=round(price_drift * 100, 2),
        )
        return None

    add_qty = math.floor(position.qty * 0.5)
    if add_qty == 0:
        log.debug("pyramid_position: add_qty = 0", symbol=result.symbol)
        return None

    # ── Cash check ────────────────────────────────────────────────────────────
    try:
        state = get_portfolio_state(db_path)
    except Exception as exc:
        log.warning(
            "pyramid_position: could not load portfolio state",
            symbol=result.symbol,
            error=str(exc),
        )
        return None

    cost = add_qty * result.entry_price
    if cost > state.cash:
        log.debug(
            "pyramid_position: insufficient cash for add-on",
            symbol=result.symbol,
            cost=cost,
            cash=state.cash,
        )
        return None

    add_trade = Trade(
        symbol=result.symbol,
        entry_date=date.today(),
        entry_price=result.entry_price,
        qty=add_qty,
        stop_loss=position.stop_loss,
        target_price=result.target_price,
        risk_pct=result.risk_pct,
        rr_ratio=result.rr_ratio,
        setup_quality=result.setup_quality,
        score=result.score,
        pyramided=True,
        status="open",
    )

    if is_market_open():
        try:
            opened = open_position(db_path, add_trade)
        except Exception as exc:
            log.warning(
                "pyramid_position: open_position failed",
                symbol=result.symbol,
                error=str(exc),
            )
            return None

        # Mark original as pyramided so we don't do it again
        try:
            mark_pyramided(db_path, position.id)  # type: ignore[arg-type]
        except Exception as exc:
            log.warning(
                "pyramid_position: mark_pyramided failed",
                symbol=result.symbol,
                trade_id=position.id,
                error=str(exc),
            )

        log.info(
            "pyramid_position: add-on opened",
            symbol=result.symbol,
            add_qty=add_qty,
            entry_price=result.entry_price,
        )
        return opened
    else:
        try:
            queue_order(
                db_path=db_path,
                symbol=result.symbol,
                order_type="pyramid",
                entry_price=result.entry_price,
                stop_loss=position.stop_loss,
                target_price=result.target_price,
                risk_pct=result.risk_pct,
                rr_ratio=result.rr_ratio,
                setup_quality=result.setup_quality,
                score=result.score,
                expiry_days=cfg["expiry_days"],
            )
            log.info(
                "pyramid_position: add-on queued (market closed)",
                symbol=result.symbol,
                add_qty=add_qty,
            )
        except Exception as exc:
            log.warning(
                "pyramid_position: queue_order failed",
                symbol=result.symbol,
                error=str(exc),
            )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# check_exits
# ─────────────────────────────────────────────────────────────────────────────

def check_exits(
    current_prices: dict[str, float],
    db_path: Path,
    config: dict,
) -> list[Trade]:
    """
    Evaluate stop-loss and target-price exits for all open positions.

    For each open position:
      - If current_price <= stop_loss  → close with reason 'stop_loss'
      - If target_price set AND
           current_price >= target_price → close with reason 'target'

    Symbols absent from current_prices are silently skipped.

    Returns list of Trade objects that were closed in this call.
    """
    closed: list[Trade] = []

    try:
        positions = get_open_positions(db_path)
    except Exception as exc:
        log.warning("check_exits: could not load open positions", error=str(exc))
        return closed

    for position in positions:
        if position.symbol not in current_prices:
            continue

        current_price = current_prices[position.symbol]

        try:
            if current_price <= position.stop_loss:
                trade = close_position(
                    db_path=db_path,
                    trade_id=position.id,       # type: ignore[arg-type]
                    exit_date=date.today(),
                    exit_price=current_price,
                    exit_reason="stop_loss",
                )
                closed.append(trade)
                log.info(
                    "check_exits: stop-loss triggered",
                    symbol=position.symbol,
                    stop_loss=position.stop_loss,
                    current_price=current_price,
                )
            elif position.target_price and current_price >= position.target_price:
                trade = close_position(
                    db_path=db_path,
                    trade_id=position.id,       # type: ignore[arg-type]
                    exit_date=date.today(),
                    exit_price=current_price,
                    exit_reason="target",
                )
                closed.append(trade)
                log.info(
                    "check_exits: target reached",
                    symbol=position.symbol,
                    target_price=position.target_price,
                    current_price=current_price,
                )
        except Exception as exc:
            log.warning(
                "check_exits: close_position failed",
                symbol=position.symbol,
                trade_id=position.id,
                error=str(exc),
            )

    return closed


# ─────────────────────────────────────────────────────────────────────────────
# process_screen_results  (pipeline entry point)
# ─────────────────────────────────────────────────────────────────────────────

def process_screen_results(
    results: list[SEPAResult],
    db_path: Path,
    config: dict,
) -> dict:
    """
    High-level function called by the pipeline runner after each screening run.

    Iterates results (caller should pass them sorted by score descending) and:
      1. Tries enter_trade() for each result.
      2. Tries pyramid_position() for each result (silently skips if not held).

    Each result is wrapped in its own try/except — one failure cannot stop
    the rest from being processed.

    Returns a summary dict:
        {
            "entered":  <int>,   # new positions opened (market was open)
            "pyramided": <int>,  # pyramid add-ons opened (market was open)
            "queued":   <int>,   # orders queued (market was closed)
            "skipped":  <int>,   # results that did not pass any gate
        }
    """
    entered   = 0
    pyramided = 0
    queued    = 0
    skipped   = 0

    for result in results:
        try:
            trade = enter_trade(result, db_path, config)
            if trade is not None:
                entered += 1
                continue  # already entered; no point pyramiding fresh entry

            # Detect whether an order was queued (market was closed path)
            # by checking if a pending order now exists for this symbol.
            # We do this only if enter_trade returned None but score passed.
            cfg = _get_config_values(config)
            score_ok    = result.score >= cfg["min_score_to_trade"]
            quality_ok  = result.setup_quality in ("A+", "A")
            prices_ok   = result.entry_price is not None and result.stop_loss is not None
            if score_ok and quality_ok and prices_ok and not is_market_open():
                # A queue attempt was made inside enter_trade if we held no position
                # We count it as queued only when we didn't already hold the symbol
                try:
                    existing = get_position(db_path, result.symbol)
                except Exception:
                    existing = None
                if existing is None:
                    queued += 1
                    continue

        except Exception as exc:
            log.warning(
                "process_screen_results: enter_trade error",
                symbol=result.symbol,
                error=str(exc),
            )

        try:
            pyr_trade = pyramid_position(result, db_path, config)
            if pyr_trade is not None:
                pyramided += 1
                continue
        except Exception as exc:
            log.warning(
                "process_screen_results: pyramid_position error",
                symbol=result.symbol,
                error=str(exc),
            )

        skipped += 1

    summary = {
        "entered":   entered,
        "pyramided": pyramided,
        "queued":    queued,
        "skipped":   skipped,
    }
    log.info("process_screen_results complete", **summary)
    return summary
