"""
tests/unit/test_paper_trading.py
─────────────────────────────────
Unit tests for Phase 7: paper_trading/{portfolio,order_queue,simulator,report}.py

All tests use in-memory / tmp_path SQLite — no live network or file I/O.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from paper_trading.order_queue import (
    cancel_expired_orders,
    execute_pending_orders,
    get_pending_orders,
    init_order_queue_table,
    is_market_open,
    queue_order,
)
from paper_trading.portfolio import (
    Trade,
    get_open_positions,
    get_portfolio_state,
    init_paper_trading_tables,
    open_position,
    close_position,
    reset_portfolio,
)
from paper_trading.report import (
    format_summary_text,
    get_portfolio_summary,
)
from paper_trading.simulator import (
    check_exits,
    enter_trade,
)
from rules.scorer import SEPAResult
from utils.exceptions import PaperTradingError

IST = ZoneInfo("Asia/Kolkata")

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a fully-initialised paper-trading DB in a temp dir."""
    db = tmp_path / "paper_test.db"
    init_paper_trading_tables(db)
    init_order_queue_table(db)
    return db


def _default_sim_config(**overrides) -> dict:
    cfg = {
        "paper_trading": {
            "enabled": True,
            "initial_capital": 100_000,
            "max_positions": 10,
            "risk_per_trade_pct": 2.0,
            "min_score_to_trade": 70,
            "min_confidence": 50,
            "expiry_days": 2,
        }
    }
    cfg["paper_trading"].update(overrides)
    return cfg


def make_sepa_result(**overrides) -> SEPAResult:
    """Build a valid A+ SEPAResult with sensible defaults."""
    defaults = dict(
        symbol="TESTSTOCK",
        date=date.today(),
        stage=2,
        stage_label="Stage 2 — Advancing",
        stage_confidence=100,
        trend_template_pass=True,
        trend_template_details={f"C{i}": True for i in range(1, 9)},
        conditions_met=8,
        vcp_qualified=True,
        vcp_grade="A",
        vcp_details={
            "quality_grade": "A",
            "vol_ratio": 0.3,
            "contraction_count": 3,
            "max_depth_pct": 15.0,
            "final_depth_pct": 3.0,
            "base_weeks": 10,
            "fail_reason": None,
        },
        breakout_triggered=True,
        entry_price=1000.0,
        stop_loss=900.0,
        stop_type="vcp_base",
        risk_pct=10.0,
        rr_ratio=2.0,
        target_price=1200.0,
        reward_pct=20.0,
        has_resistance=True,
        rs_rating=90,
        setup_quality="A+",
        score=88,
    )
    defaults.update(overrides)
    return SEPAResult(**defaults)


def _make_trade(
    symbol: str = "TESTSTOCK",
    entry_price: float = 1000.0,
    qty: int = 10,
    stop_loss: float = 900.0,
    target_price: float | None = 1200.0,
) -> Trade:
    return Trade(
        symbol=symbol,
        entry_date=date.today(),
        entry_price=entry_price,
        qty=qty,
        stop_loss=stop_loss,
        target_price=target_price,
        setup_quality="A+",
        score=88,
    )


def _ist(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


# ─────────────────────────────────────────────────────────────────────────────
# portfolio.py tests
# ─────────────────────────────────────────────────────────────────────────────

class TestInitTables:
    def test_init_creates_tables(self, tmp_path: Path):
        db = tmp_path / "init_test.db"
        init_paper_trading_tables(db)
        conn = sqlite3.connect(str(db))
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "paper_positions" in tables
        assert "paper_portfolio_state" in tables

    def test_init_is_idempotent(self, tmp_path: Path):
        db = tmp_path / "idem_test.db"
        init_paper_trading_tables(db)
        init_paper_trading_tables(db)   # second call must not raise


class TestOpenPosition:
    def test_open_position_deducts_cash(self, db_path: Path):
        state_before = get_portfolio_state(db_path)
        trade = _make_trade(entry_price=1000.0, qty=5)
        open_position(db_path, trade)
        state_after = get_portfolio_state(db_path)
        assert state_after.cash == pytest.approx(state_before.cash - 5 * 1000.0)

    def test_open_position_insufficient_cash_raises(self, db_path: Path):
        # Cost = 1000 * 200 = 200_000 > default initial_capital of 100_000
        big_trade = _make_trade(entry_price=1000.0, qty=200)
        with pytest.raises(PaperTradingError):
            open_position(db_path, big_trade)


class TestClosePosition:
    def test_close_position_adds_cash_and_records_pnl(self, db_path: Path):
        trade = open_position(db_path, _make_trade(entry_price=1000.0, qty=10))
        state_mid = get_portfolio_state(db_path)

        closed = close_position(db_path, trade.id, date.today(), 1100.0, "target")

        state_after = get_portfolio_state(db_path)
        expected_proceeds = 1100.0 * 10
        assert state_after.cash == pytest.approx(state_mid.cash + expected_proceeds)
        assert closed.pnl == pytest.approx((1100.0 - 1000.0) * 10)
        assert closed.pnl > 0
        assert closed.status == "closed"
        assert state_after.win_trades == 1

    def test_close_position_losing_trade(self, db_path: Path):
        trade = open_position(db_path, _make_trade(entry_price=1000.0, qty=10))
        state_before = get_portfolio_state(db_path)

        closed = close_position(db_path, trade.id, date.today(), 850.0, "stop_loss")
        state_after = get_portfolio_state(db_path)

        assert closed.pnl < 0
        assert state_after.win_trades == state_before.win_trades   # NOT incremented


class TestResetPortfolio:
    def test_reset_portfolio_clears_state(self, db_path: Path):
        open_position(db_path, _make_trade("STOCK1", entry_price=100.0, qty=5))
        open_position(db_path, _make_trade("STOCK2", entry_price=200.0, qty=3))
        assert len(get_open_positions(db_path)) == 2

        reset_portfolio(db_path, 100_000.0)

        assert get_open_positions(db_path) == []
        state = get_portfolio_state(db_path)
        assert state.cash == pytest.approx(100_000.0)


# ─────────────────────────────────────────────────────────────────────────────
# order_queue.py tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIsMarketOpen:
    def test_is_market_open_true(self):
        # Wednesday 2025-01-08 10:00 IST
        assert is_market_open(_ist(2025, 1, 8, 10, 0)) is True

    def test_is_market_open_false_weekend(self):
        # Saturday 2025-01-11
        assert is_market_open(_ist(2025, 1, 11, 11, 0)) is False

    def test_is_market_open_false_before_hours(self):
        # Monday 2025-01-06 08:00 IST (before 09:15)
        assert is_market_open(_ist(2025, 1, 6, 8, 0)) is False

    def test_is_market_open_false_after_hours(self):
        # Friday 2025-01-10 15:31 IST (after 15:30)
        assert is_market_open(_ist(2025, 1, 10, 15, 31)) is False


class TestQueueOrder:
    def test_queue_order_persists(self, db_path: Path):
        queue_order(
            db_path=db_path,
            symbol="QUEUED",
            order_type="enter",
            entry_price=500.0,
            stop_loss=450.0,
            target_price=600.0,
            risk_pct=10.0,
            rr_ratio=2.0,
            setup_quality="A+",
            score=88,
            expiry_days=2,
        )
        orders = get_pending_orders(db_path)
        assert len(orders) == 1
        assert orders[0].symbol == "QUEUED"
        assert orders[0].entry_price == pytest.approx(500.0)
        assert orders[0].stop_loss == pytest.approx(450.0)

    def test_queue_order_duplicate_replaces(self, db_path: Path):
        for _ in range(2):
            queue_order(
                db_path=db_path,
                symbol="DUPE",
                order_type="enter",
                entry_price=500.0,
                stop_loss=450.0,
                target_price=600.0,
                risk_pct=10.0,
                rr_ratio=2.0,
                setup_quality="A+",
                score=88,
            )
        assert len(get_pending_orders(db_path)) == 1


class TestCancelExpiredOrders:
    def test_cancel_expired_orders(self, db_path: Path):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO paper_pending_orders
                (symbol, order_type, entry_price, stop_loss, expires_at)
            VALUES ('EXPIRED', 'enter', 1000.0, 900.0, ?)
            """,
            (yesterday,),
        )
        conn.commit()
        conn.close()

        count = cancel_expired_orders(db_path)
        assert count == 1
        assert get_pending_orders(db_path) == []


class TestExecutePendingOrders:
    def test_execute_pending_orders_fills_at_current_price(self, db_path: Path):
        queue_order(
            db_path=db_path,
            symbol="FILLME",
            order_type="enter",
            entry_price=1000.0,
            stop_loss=900.0,
            target_price=1200.0,
            risk_pct=10.0,
            rr_ratio=2.0,
            setup_quality="A+",
            score=88,
            expiry_days=5,
        )
        fill_price = 1010.0
        cfg = _default_sim_config()
        filled = execute_pending_orders(db_path, {"FILLME": fill_price}, cfg)

        assert len(filled) == 1
        assert filled[0].entry_price == pytest.approx(fill_price)

    def test_execute_pending_orders_skips_missing_price(self, db_path: Path):
        queue_order(
            db_path=db_path,
            symbol="NOPRICE",
            order_type="enter",
            entry_price=1000.0,
            stop_loss=900.0,
            target_price=None,
            risk_pct=None,
            rr_ratio=None,
            setup_quality="A+",
            score=88,
            expiry_days=5,
        )
        cfg = _default_sim_config()
        filled = execute_pending_orders(db_path, {}, cfg)

        assert filled == []
        assert len(get_pending_orders(db_path)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# simulator.py tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEnterTrade:
    def test_enter_trade_success_when_market_open(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        result = make_sepa_result()
        trade = enter_trade(result, db_path, _default_sim_config())
        assert trade is not None
        assert isinstance(trade, Trade)
        assert len(get_open_positions(db_path)) == 1

    def test_enter_trade_queued_when_market_closed(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: False)
        result = make_sepa_result()
        trade = enter_trade(result, db_path, _default_sim_config())
        assert trade is None
        assert len(get_pending_orders(db_path)) == 1

    def test_enter_trade_rejects_low_score(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        result = make_sepa_result(score=55, setup_quality="B")
        trade = enter_trade(result, db_path, _default_sim_config())
        assert trade is None

    def test_enter_trade_rejects_b_quality(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        result = make_sepa_result(score=75, setup_quality="B")
        trade = enter_trade(result, db_path, _default_sim_config())
        assert trade is None

    def test_enter_trade_rejects_duplicate_symbol(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        result = make_sepa_result(symbol="DUPESYM")
        first = enter_trade(result, db_path, _default_sim_config())
        assert first is not None
        second = enter_trade(result, db_path, _default_sim_config())
        assert second is None

    def test_enter_trade_rejects_when_max_positions_full(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        cfg = _default_sim_config(max_positions=1)
        first = enter_trade(make_sepa_result(symbol="STOCK1"), db_path, cfg)
        assert first is not None
        second = enter_trade(make_sepa_result(symbol="STOCK2"), db_path, cfg)
        assert second is None


class TestCheckExits:
    def test_check_exits_stop_loss_hit(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        result = make_sepa_result(symbol="STOPME", entry_price=1000.0, stop_loss=900.0)
        entered = enter_trade(result, db_path, _default_sim_config())
        assert entered is not None

        closed = check_exits({"STOPME": 850.0}, db_path, _default_sim_config())

        assert len(closed) == 1
        assert closed[0].exit_reason == "stop_loss"
        assert closed[0].pnl < 0

    def test_check_exits_target_hit(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        result = make_sepa_result(
            symbol="TARGETME", entry_price=1000.0, stop_loss=900.0, target_price=1200.0
        )
        enter_trade(result, db_path, _default_sim_config())

        closed = check_exits({"TARGETME": 1250.0}, db_path, _default_sim_config())

        assert len(closed) == 1
        assert closed[0].exit_reason == "target"
        assert closed[0].pnl > 0

    def test_check_exits_no_hit(self, db_path, monkeypatch):
        monkeypatch.setattr("paper_trading.simulator.is_market_open", lambda: True)
        result = make_sepa_result(
            symbol="NOEXIT", entry_price=1000.0, stop_loss=900.0, target_price=1200.0
        )
        enter_trade(result, db_path, _default_sim_config())

        closed = check_exits({"NOEXIT": 1050.0}, db_path, _default_sim_config())
        assert closed == []


# ─────────────────────────────────────────────────────────────────────────────
# report.py tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPortfolioSummary:
    def test_portfolio_summary_empty(self, db_path: Path):
        summary = get_portfolio_summary(db_path)
        state = get_portfolio_state(db_path)
        assert summary.total_value == pytest.approx(state.initial_capital)
        assert summary.open_trades == 0
        assert summary.closed_trades == 0
        assert summary.win_rate == pytest.approx(0.0)

    def test_portfolio_summary_with_open_position(self, db_path: Path):
        trade = _make_trade(symbol="OPENPOS", entry_price=1000.0, qty=10)
        open_position(db_path, trade)

        summary = get_portfolio_summary(db_path, current_prices={"OPENPOS": 1100.0})
        assert summary.open_trades == 1
        assert summary.unrealised_pnl == pytest.approx((1100.0 - 1000.0) * 10)
        assert summary.unrealised_pnl > 0

    def test_portfolio_summary_win_rate(self, db_path: Path):
        # Trade 1: win +200
        t1 = open_position(db_path, _make_trade("WIN1", entry_price=1000.0, qty=10))
        close_position(db_path, t1.id, date.today(), 1020.0, "target")

        # Trade 2: loss -100
        t2 = open_position(db_path, _make_trade("LOSE1", entry_price=1000.0, qty=10))
        close_position(db_path, t2.id, date.today(), 990.0, "stop_loss")

        summary = get_portfolio_summary(db_path)
        assert summary.win_rate == pytest.approx(50.0)
        assert summary.realised_pnl == pytest.approx(200.0 - 100.0)

    def test_format_summary_text_not_empty(self, db_path: Path):
        summary = get_portfolio_summary(db_path)
        text = format_summary_text(summary)
        assert isinstance(text, str)
        assert len(text) > 0
        assert "Paper Portfolio" in text
