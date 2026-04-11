"""
backtest/portfolio.py
─────────────────────
Pure in-memory portfolio for the Minervini AI walk-forward backtester.

Design rules (PROJECT_DESIGN.md §14 Phase 8)
─────────────────────────────────────────────
  • All state lives in RAM — zero I/O, zero SQLite, zero Parquet.
  • Position sizing: 1R = position_size_pct of current portfolio value.
  • Max open positions is capped (default 10, from paper_trading.max_positions).
  • No lookahead bias — only prices passed in externally are used.
  • Trailing stop follows peak close upward but is FLOORED at initial_stop.
  • initial_risk per trade = (entry_price - initial_stop) * qty.

Config keys consumed
────────────────────
  config["backtest"]["position_size_pct"]   – fraction of portfolio risked per trade (default 0.02)
  config["paper_trading"]["max_positions"]  – cap on simultaneous open trades (default 10)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from utils.exceptions import BacktestError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_RISK_PCT: float = 0.02   # 1R = 2 % of portfolio value
_DEFAULT_MAX_POS: int   = 10

# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestPosition:
    """One open or closed position in the backtest portfolio."""

    symbol:        str
    entry_date:    date
    entry_price:   float
    qty:           int
    initial_stop:  float           # VCP base_low / ATR stop — never changes
    current_stop:  float           # trailing stop — only ever moves up
    target_price:  Optional[float]
    setup_quality: str
    score:         int
    regime:        Optional[str]   # label from regime.py
    initial_risk:  float           # (entry_price - initial_stop) * qty
    peak_price:    float           # highest close seen while holding
    status:        str             # 'open' | 'closed'

    exit_date:    Optional[date]  = None
    exit_price:   Optional[float] = None
    exit_reason:  Optional[str]   = None  # 'trailing_stop'|'fixed_stop'|'target'|'max_hold'|'end_of_data'
    pnl:          Optional[float] = None
    pnl_pct:      Optional[float] = None
    r_multiple:   Optional[float] = None  # pnl / initial_risk


@dataclass
class BacktestPortfolioState:
    """Snapshot of the backtest portfolio at any point in time."""

    cash:             float
    initial_capital:  float
    open_positions:   list[BacktestPosition] = field(default_factory=list)
    closed_positions: list[BacktestPosition] = field(default_factory=list)

    @property
    def portfolio_value(self) -> float:
        """
        Conservative mark-to-market value.

        cash + sum(current_stop * qty) for all open positions.
        Using current_stop (not market price) provides a floor-based
        conservative estimate that never overstates the portfolio value.
        """
        open_value = sum(p.current_stop * p.qty for p in self.open_positions)
        return self.cash + open_value

    @property
    def open_count(self) -> int:
        return len(self.open_positions)


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio class
# ─────────────────────────────────────────────────────────────────────────────

class BacktestPortfolio:
    """
    Pure in-memory portfolio for one backtest run.

    All methods are side-effect-free with respect to the outside world.
    The only state mutated is the internal BacktestPortfolioState.
    """

    def __init__(self, initial_capital: float, config: dict) -> None:
        if initial_capital <= 0:
            raise BacktestError(
                "initial_capital must be positive",
                initial_capital=initial_capital,
            )

        bt_cfg  = config.get("backtest", {})
        pt_cfg  = config.get("paper_trading", {})

        self._risk_pct:    float = float(bt_cfg.get("position_size_pct", _DEFAULT_RISK_PCT))
        self._max_pos:     int   = int(pt_cfg.get("max_positions", _DEFAULT_MAX_POS))

        self.state = BacktestPortfolioState(
            cash=initial_capital,
            initial_capital=initial_capital,
        )
        log.debug(
            "BacktestPortfolio initialised",
            initial_capital=initial_capital,
            risk_pct=self._risk_pct,
            max_positions=self._max_pos,
        )

    # ── Read-only helpers ────────────────────────────────────────────────────

    @property
    def _open(self) -> list[BacktestPosition]:
        return self.state.open_positions

    @property
    def _closed(self) -> list[BacktestPosition]:
        return self.state.closed_positions

    # ── Sizing ───────────────────────────────────────────────────────────────

    def compute_qty(self, entry_price: float, stop_loss: float) -> int:
        """
        Compute position size using the 1R formula.

        qty = floor(portfolio_value * risk_pct / (entry_price - stop_loss))

        Clamped so that qty * entry_price <= available cash.
        Returns 0 if stop_loss >= entry_price (invalid setup).
        """
        per_share_risk = entry_price - stop_loss
        if per_share_risk <= 0:
            log.debug(
                "compute_qty: stop >= entry, returning 0",
                entry=entry_price,
                stop=stop_loss,
            )
            return 0

        risk_budget = self.state.portfolio_value * self._risk_pct
        qty = math.floor(risk_budget / per_share_risk)

        # Clamp to available cash
        if qty > 0 and entry_price > 0:
            max_by_cash = math.floor(self.state.cash / entry_price)
            qty = min(qty, max_by_cash)

        return max(qty, 0)


    def can_enter(self, entry_price: float, stop_loss: float) -> bool:
        """
        Return True iff:
          - open position count < max_positions, AND
          - the cost of one position (qty * entry_price) fits in available cash.
        """
        if self.state.open_count >= self._max_pos:
            log.debug("can_enter: max_positions reached", count=self.state.open_count)
            return False

        qty = self.compute_qty(entry_price, stop_loss)
        if qty <= 0:
            log.debug("can_enter: qty=0, cannot enter")
            return False

        cost = qty * entry_price
        if cost > self.state.cash:
            log.debug(
                "can_enter: insufficient cash",
                cost=cost,
                cash=self.state.cash,
            )
            return False

        return True

    # ── Entry ────────────────────────────────────────────────────────────────

    def enter(
        self,
        symbol: str,
        entry_date: date,
        entry_price: float,
        stop_loss: float,
        target_price: Optional[float],
        setup_quality: str,
        score: int,
        regime: Optional[str],
    ) -> Optional[BacktestPosition]:
        """
        Open a new position.

        Returns None if can_enter() is False.
        Deducts entry cost from cash.
        initial_stop = current_stop = stop_loss (trailing starts here).
        peak_price   = entry_price.
        """
        if not self.can_enter(entry_price, stop_loss):
            return None

        qty = self.compute_qty(entry_price, stop_loss)
        cost = qty * entry_price
        initial_risk = (entry_price - stop_loss) * qty

        pos = BacktestPosition(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            qty=qty,
            initial_stop=stop_loss,
            current_stop=stop_loss,
            target_price=target_price,
            setup_quality=setup_quality,
            score=score,
            regime=regime,
            initial_risk=initial_risk,
            peak_price=entry_price,
            status="open",
        )

        self.state.cash -= cost
        self._open.append(pos)

        log.debug(
            "Position entered",
            symbol=symbol,
            entry_price=entry_price,
            qty=qty,
            cost=round(cost, 2),
            cash_remaining=round(self.state.cash, 2),
        )
        return pos


    # ── Daily update ─────────────────────────────────────────────────────────

    def update_trailing_stops(
        self,
        current_prices: dict[str, float],
        trailing_stop_pct: Optional[float],
    ) -> None:
        """
        Update peak_price and current_stop for each open position.

        If trailing_stop_pct is provided:
            trail     = new_peak * (1 - trailing_stop_pct)
            new_stop  = max(trail, position.initial_stop)   ← floor at initial_stop
        Else (fixed stop):
            new_stop  = position.initial_stop

        Symbols absent from current_prices are skipped silently.
        current_stop only ever moves upward (new_stop >= current_stop enforced).
        """
        for pos in self._open:
            price = current_prices.get(pos.symbol)
            if price is None:
                continue

            new_peak = max(pos.peak_price, price)
            pos.peak_price = new_peak

            if trailing_stop_pct is not None:
                trail    = new_peak * (1.0 - trailing_stop_pct)
                new_stop = max(trail, pos.initial_stop)   # floor
            else:
                new_stop = pos.initial_stop               # fixed

            # Monotonic: stop only moves up
            pos.current_stop = max(new_stop, pos.current_stop)

    # ── Exit logic ───────────────────────────────────────────────────────────

    def _close_position(
        self,
        pos: BacktestPosition,
        exit_price: float,
        exit_date: date,
        exit_reason: str,
    ) -> None:
        """Mutate *pos* to closed state and credit cash. Removes from _open."""
        pnl     = (exit_price - pos.entry_price) * pos.qty
        pnl_pct = (exit_price / pos.entry_price - 1.0) * 100.0 if pos.entry_price else 0.0
        r_mult  = pnl / pos.initial_risk if pos.initial_risk != 0 else None
        proceeds = exit_price * pos.qty

        pos.exit_price  = exit_price
        pos.exit_date   = exit_date
        pos.exit_reason = exit_reason
        pos.pnl         = round(pnl, 6)
        pos.pnl_pct     = round(pnl_pct, 6)
        pos.r_multiple  = round(r_mult, 6) if r_mult is not None else None
        pos.status      = "closed"

        self.state.cash += proceeds
        self._open.remove(pos)
        self._closed.append(pos)

        log.debug(
            "Position closed",
            symbol=pos.symbol,
            exit_reason=exit_reason,
            pnl=round(pnl, 2),
            r_multiple=pos.r_multiple,
        )


    def check_exits(
        self,
        current_prices: dict[str, float],
        current_date: date,
        max_hold_days: int,
    ) -> list[BacktestPosition]:
        """
        Scan all open positions and close those that hit an exit condition.

        Exit priority (evaluated in order):
          1. Stop hit  : current_price <= current_stop
             reason    = 'trailing_stop' if current_stop > initial_stop
                         else 'fixed_stop'
          2. Target hit: current_price >= target_price (if target set)
             reason    = 'target'
          3. Max hold  : (current_date - entry_date).days >= max_hold_days
             reason    = 'max_hold'
             exit_price = current_price (or entry_price if not in current_prices)

        Returns the list of newly closed BacktestPosition objects.
        """
        to_close: list[tuple[BacktestPosition, float, str]] = []

        for pos in list(self._open):  # iterate a snapshot
            price = current_prices.get(pos.symbol)

            # 1. Stop hit
            if price is not None and price <= pos.current_stop:
                reason = (
                    "trailing_stop" if pos.current_stop > pos.initial_stop
                    else "fixed_stop"
                )
                to_close.append((pos, pos.current_stop, reason))
                continue

            # 2. Target hit
            if (
                price is not None
                and pos.target_price is not None
                and price >= pos.target_price
            ):
                to_close.append((pos, pos.target_price, "target"))
                continue

            # 3. Max hold days
            hold_days = (current_date - pos.entry_date).days
            if hold_days >= max_hold_days:
                exit_px = price if price is not None else pos.entry_price
                to_close.append((pos, exit_px, "max_hold"))

        newly_closed: list[BacktestPosition] = []
        for pos, exit_px, reason in to_close:
            self._close_position(pos, exit_px, current_date, reason)
            newly_closed.append(pos)

        return newly_closed

    def close_all(
        self,
        current_prices: dict[str, float],
        current_date: date,
    ) -> list[BacktestPosition]:
        """
        Force-close every open position at current_prices.

        Positions whose symbol is absent from current_prices are closed at
        their entry_price (best safe default — no lookahead used).
        exit_reason = 'end_of_data'.
        """
        newly_closed: list[BacktestPosition] = []
        for pos in list(self._open):
            exit_px = current_prices.get(pos.symbol, pos.entry_price)
            self._close_position(pos, exit_px, current_date, "end_of_data")
            newly_closed.append(pos)
        return newly_closed

    # ── Output ───────────────────────────────────────────────────────────────

    def to_trade_list(self) -> list[dict]:
        """
        Return all closed positions as a list of plain dicts for metrics.py.

        All BacktestPosition fields are included, plus 'initial_risk'
        is kept as a top-level key (metrics.py requires it explicitly).
        """
        result: list[dict] = []
        for pos in self._closed:
            result.append({
                "symbol":        pos.symbol,
                "entry_date":    pos.entry_date,
                "entry_price":   pos.entry_price,
                "qty":           pos.qty,
                "initial_stop":  pos.initial_stop,
                "current_stop":  pos.current_stop,
                "target_price":  pos.target_price,
                "setup_quality": pos.setup_quality,
                "score":         pos.score,
                "regime":        pos.regime,
                "initial_risk":  pos.initial_risk,
                "peak_price":    pos.peak_price,
                "status":        pos.status,
                "exit_date":     pos.exit_date,
                "exit_price":    pos.exit_price,
                "exit_reason":   pos.exit_reason,
                "pnl":           pos.pnl,
                "pnl_pct":       pos.pnl_pct,
                "r_multiple":    pos.r_multiple,
            })
        return result
