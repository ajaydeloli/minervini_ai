"""
tests/unit/test_backtest_portfolio.py
──────────────────────────────────────
Unit tests for backtest/portfolio.py.

Coverage (≥ 15 tests)
─────────────────────
  test_compute_qty_basic                      — 2% risk, known entry/stop → correct qty
  test_compute_qty_stop_gte_entry             — stop >= entry → returns 0
  test_compute_qty_stop_equal_entry           — stop == entry → returns 0
  test_compute_qty_clamped_by_cash            — qty capped when cash is tight
  test_can_enter_max_positions_reached        — max_positions cap → False
  test_can_enter_insufficient_cash            — not enough cash → False
  test_can_enter_happy_path                   — valid setup → True
  test_enter_deducts_correct_cost             — cash decremented by qty * entry_price
  test_enter_returns_none_when_cannot_enter   — None when can_enter() is False
  test_enter_initial_current_stop_equal       — initial_stop == current_stop == stop_loss
  test_update_trailing_stop_rises             — trailing stop rises with price
  test_update_trailing_stop_floor_at_initial  — trailing stop never drops below initial_stop
  test_update_trailing_stop_none_pct          — trailing_stop_pct=None → stop stays fixed
  test_update_trailing_stop_skips_unknown     — symbols absent from prices are skipped
  test_check_exits_stop_loss_trigger          — stop hit → closed with fixed_stop reason
  test_check_exits_trailing_stop_reason       — raised stop hit → 'trailing_stop' reason
  test_check_exits_target_trigger             — target hit → 'target' reason
  test_check_exits_max_hold_trigger           — hold days >= max → 'max_hold' reason
  test_close_all_end_of_data                  — all open closed with 'end_of_data'
  test_to_trade_list_pnl_and_r_multiple       — pnl and r_multiple correct on closed
  test_portfolio_value_mixed_positions        — value = cash + sum(stop * qty) for open
  test_negative_initial_capital_raises        — BacktestError on bad capital
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from backtest.portfolio import BacktestPortfolio, BacktestPosition
from utils.exceptions import BacktestError

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ─────────────────────────────────────────────────────────────────────────────

_BASE_CONFIG = {
    "backtest": {
        "position_size_pct": 0.02,   # 2% risk per trade
        "max_hold_days": 20,
    },
    "paper_trading": {
        "max_positions": 3,
    },
}

_CAPITAL = 100_000.0
_TODAY   = date(2024, 6, 1)


def _make_portfolio(capital: float = _CAPITAL, config: dict | None = None) -> BacktestPortfolio:
    return BacktestPortfolio(capital, config or _BASE_CONFIG)


def _open_one(
    port: BacktestPortfolio,
    symbol: str = "RELIANCE",
    entry_price: float = 100.0,
    stop_loss: float   = 90.0,
    entry_date: date   = _TODAY,
) -> BacktestPosition | None:
    return port.enter(
        symbol=symbol,
        entry_date=entry_date,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_price=120.0,
        setup_quality="A+",
        score=85,
        regime="Bull",
    )


# ─────────────────────────────────────────────────────────────────────────────
# compute_qty
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_qty_basic():
    """
    risk_budget = 100_000 * 0.02 = 2_000
    per_share_risk = 100 - 90 = 10
    qty = floor(2_000 / 10) = 200
    """
    port = _make_portfolio()
    qty = port.compute_qty(entry_price=100.0, stop_loss=90.0)
    assert qty == 200


def test_compute_qty_stop_gte_entry():
    port = _make_portfolio()
    assert port.compute_qty(entry_price=100.0, stop_loss=110.0) == 0


def test_compute_qty_stop_equal_entry():
    port = _make_portfolio()
    assert port.compute_qty(entry_price=100.0, stop_loss=100.0) == 0


def test_compute_qty_clamped_by_cash():
    """
    With only ₹500 cash, qty * 100 cannot exceed 5 shares
    even if risk formula says more.
    """
    cfg = {
        "backtest": {"position_size_pct": 0.50},   # 50% risk → huge formula qty
        "paper_trading": {"max_positions": 10},
    }
    port = _make_portfolio(capital=500.0, config=cfg)
    qty = port.compute_qty(entry_price=100.0, stop_loss=90.0)
    assert qty * 100.0 <= 500.0

# ─────────────────────────────────────────────────────────────────────────────
# can_enter
# ─────────────────────────────────────────────────────────────────────────────

def test_can_enter_happy_path():
    port = _make_portfolio()
    assert port.can_enter(entry_price=100.0, stop_loss=90.0) is True


def test_can_enter_max_positions_reached():
    port = _make_portfolio()
    # Fill all 3 slots with different symbols
    for sym in ("SYM1", "SYM2", "SYM3"):
        pos = _open_one(port, symbol=sym)
        assert pos is not None
    assert port.can_enter(entry_price=100.0, stop_loss=90.0) is False


def test_can_enter_insufficient_cash():
    """After spending most capital, the next entry should be rejected."""
    cfg = {
        "backtest": {"position_size_pct": 0.99},   # uses almost all portfolio
        "paper_trading": {"max_positions": 10},
    }
    port = _make_portfolio(capital=1_000.0, config=cfg)
    _open_one(port, entry_price=100.0, stop_loss=90.0)
    # Cash nearly exhausted; second trade at same prices cannot fit
    assert port.can_enter(entry_price=100.0, stop_loss=90.0) is False


# ─────────────────────────────────────────────────────────────────────────────
# enter
# ─────────────────────────────────────────────────────────────────────────────

def test_enter_deducts_correct_cost():
    """
    qty = 200 (per test_compute_qty_basic)
    cost = 200 * 100 = 20_000
    remaining cash = 100_000 - 20_000 = 80_000
    """
    port = _make_portfolio()
    _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert abs(port.state.cash - 80_000.0) < 1e-6


def test_enter_returns_none_when_cannot_enter():
    port = _make_portfolio()
    for sym in ("S1", "S2", "S3"):
        _open_one(port, symbol=sym)
    result = _open_one(port, symbol="S4")
    assert result is None


def test_enter_initial_current_stop_equal():
    port = _make_portfolio()
    pos = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    assert pos.initial_stop == 90.0
    assert pos.current_stop == 90.0
    assert pos.initial_stop == pos.current_stop


def test_enter_peak_price_set_to_entry():
    port = _make_portfolio()
    pos = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    assert pos.peak_price == 100.0


def test_enter_initial_risk_correct():
    """initial_risk = (entry - stop) * qty = (100 - 90) * 200 = 2_000"""
    port = _make_portfolio()
    pos = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    assert abs(pos.initial_risk - 2_000.0) < 1e-6

# ─────────────────────────────────────────────────────────────────────────────
# update_trailing_stops
# ─────────────────────────────────────────────────────────────────────────────

def test_update_trailing_stop_rises():
    """
    Entry at 100, stop at 90.  Price moves to 120.
    With 7% trailing: trail = 120 * 0.93 = 111.6  > 90 → stop rises to 111.6.
    """
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    port.update_trailing_stops({"RELIANCE": 120.0}, trailing_stop_pct=0.07)
    expected_stop = 120.0 * 0.93
    assert abs(pos.current_stop - expected_stop) < 1e-6


def test_update_trailing_stop_floor_at_initial():
    """
    With a large trailing_stop_pct, the computed trail falls below initial_stop.
    Verify the floor: current_stop must never drop below initial_stop.

    entry=100, stop=90, trailing_stop_pct=0.15
    peak stays at 100 (price didn't rise), trail = 100 * 0.85 = 85.0 < 90 → floor kicks in.
    Expect current_stop == initial_stop == 90.0.
    """
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    # trail = 100 * (1 - 0.15) = 85.0 < initial_stop (90) → floor to 90
    port.update_trailing_stops({"RELIANCE": 100.0}, trailing_stop_pct=0.15)
    assert pos.current_stop >= pos.initial_stop
    assert abs(pos.current_stop - 90.0) < 1e-6


def test_update_trailing_stop_none_pct():
    """trailing_stop_pct=None → current_stop stays at initial_stop."""
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    port.update_trailing_stops({"RELIANCE": 150.0}, trailing_stop_pct=None)
    assert abs(pos.current_stop - pos.initial_stop) < 1e-6


def test_update_trailing_stop_skips_unknown():
    """Symbols not in current_prices dict are silently skipped."""
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    old_stop = pos.current_stop
    port.update_trailing_stops({}, trailing_stop_pct=0.07)   # RELIANCE absent
    assert pos.current_stop == old_stop


# ─────────────────────────────────────────────────────────────────────────────
# check_exits
# ─────────────────────────────────────────────────────────────────────────────

def test_check_exits_stop_loss_trigger():
    """Price drops to stop → closed with 'fixed_stop'."""
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    # current_stop == initial_stop → 'fixed_stop'
    closed = port.check_exits({"RELIANCE": 89.0}, current_date=_TODAY, max_hold_days=20)
    assert len(closed) == 1
    assert closed[0].exit_reason == "fixed_stop"
    assert closed[0].status == "closed"


def test_check_exits_trailing_stop_reason():
    """After trailing stop rises, a lower price uses 'trailing_stop'."""
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    # Raise stop via update
    port.update_trailing_stops({"RELIANCE": 130.0}, trailing_stop_pct=0.07)
    raised_stop = pos.current_stop
    assert raised_stop > pos.initial_stop   # sanity check
    # Price falls to just below raised stop
    closed = port.check_exits(
        {"RELIANCE": raised_stop - 0.01},
        current_date=_TODAY,
        max_hold_days=20,
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == "trailing_stop"


def test_check_exits_target_trigger():
    """Price hits or exceeds target → closed with 'target'."""
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    assert pos.target_price == 120.0
    closed = port.check_exits({"RELIANCE": 120.0}, current_date=_TODAY, max_hold_days=20)
    assert len(closed) == 1
    assert closed[0].exit_reason == "target"
    assert abs(closed[0].exit_price - 120.0) < 1e-6


def test_check_exits_max_hold_trigger():
    """Position held >= max_hold_days → 'max_hold'."""
    port      = _make_portfolio()
    past_date = _TODAY - timedelta(days=20)
    pos       = _open_one(port, entry_price=100.0, stop_loss=90.0, entry_date=past_date)
    assert pos is not None
    # Price is between stop and target, but max hold reached
    closed = port.check_exits({"RELIANCE": 105.0}, current_date=_TODAY, max_hold_days=20)
    assert len(closed) == 1
    assert closed[0].exit_reason == "max_hold"


def test_check_exits_no_triggers():
    """Mid-trade price — nothing should close."""
    port = _make_portfolio()
    _open_one(port, entry_price=100.0, stop_loss=90.0)
    closed = port.check_exits({"RELIANCE": 110.0}, current_date=_TODAY, max_hold_days=20)
    assert len(closed) == 0

# ─────────────────────────────────────────────────────────────────────────────
# close_all
# ─────────────────────────────────────────────────────────────────────────────

def test_close_all_end_of_data():
    """All open positions are force-closed with exit_reason='end_of_data'."""
    port = _make_portfolio()
    for sym in ("SYM1", "SYM2"):
        _open_one(port, symbol=sym, entry_price=100.0, stop_loss=90.0)

    assert port.state.open_count == 2
    closed = port.close_all({"SYM1": 115.0, "SYM2": 92.0}, current_date=_TODAY)
    assert len(closed) == 2
    assert all(p.exit_reason == "end_of_data" for p in closed)
    assert port.state.open_count == 0


def test_close_all_missing_price_falls_back_to_entry():
    """Symbol absent from prices → exits at entry_price."""
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    port.close_all({}, current_date=_TODAY)   # no prices supplied
    assert pos.exit_price == pos.entry_price


# ─────────────────────────────────────────────────────────────────────────────
# to_trade_list — pnl and r_multiple
# ─────────────────────────────────────────────────────────────────────────────

def test_to_trade_list_pnl_and_r_multiple():
    """
    entry_price=100, qty=200, stop=90, target=120.
    Exit at 120 → pnl = (120-100)*200 = 4_000
    initial_risk = (100-90)*200 = 2_000
    r_multiple = 4_000 / 2_000 = 2.0
    """
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None

    port.check_exits({"RELIANCE": 120.0}, current_date=_TODAY, max_hold_days=20)
    trades = port.to_trade_list()

    assert len(trades) == 1
    t = trades[0]
    assert abs(t["pnl"] - 4_000.0) < 1e-3
    assert abs(t["r_multiple"] - 2.0) < 1e-6
    assert "initial_risk" in t


def test_to_trade_list_loss_r_negative():
    """Stop-loss exit → negative r_multiple."""
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None

    port.check_exits({"RELIANCE": 88.0}, current_date=_TODAY, max_hold_days=20)
    trades = port.to_trade_list()

    assert len(trades) == 1
    assert trades[0]["pnl"] < 0
    assert trades[0]["r_multiple"] < 0


def test_to_trade_list_open_positions_excluded():
    """Only closed positions appear in the trade list."""
    port = _make_portfolio()
    _open_one(port)   # leaves position open
    assert port.to_trade_list() == []

# ─────────────────────────────────────────────────────────────────────────────
# BacktestPortfolioState.portfolio_value
# ─────────────────────────────────────────────────────────────────────────────

def test_portfolio_value_mixed_positions():
    """
    After entering: cash = 80_000, open has 200 shares with stop at 90.
    portfolio_value = 80_000 + (90 * 200) = 80_000 + 18_000 = 98_000.
    """
    port = _make_portfolio()
    pos  = _open_one(port, entry_price=100.0, stop_loss=90.0)
    assert pos is not None
    expected = port.state.cash + pos.current_stop * pos.qty
    assert abs(port.state.portfolio_value - expected) < 1e-6
    assert abs(port.state.portfolio_value - 98_000.0) < 1e-6


def test_portfolio_value_no_open_positions():
    """With no open positions, portfolio_value == cash."""
    port = _make_portfolio()
    assert abs(port.state.portfolio_value - _CAPITAL) < 1e-6

# ─────────────────────────────────────────────────────────────────────────────
# Error guards
# ─────────────────────────────────────────────────────────────────────────────

def test_negative_initial_capital_raises():
    with pytest.raises(BacktestError):
        BacktestPortfolio(-1.0, _BASE_CONFIG)


def test_zero_initial_capital_raises():
    with pytest.raises(BacktestError):
        BacktestPortfolio(0.0, _BASE_CONFIG)
