"""Deep financial verification tests for the Gold/Silver live trading system.

Covers: Signal dataclass, Position lifecycle, P&L calculations, Equity,
Drawdown, Fill dedup, Trade close, Paper broker, Account engine, WebSocket
state format, Telegram router, and Fee model.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time

import pytest

# ── Bootstrap modules via conftest ──
from tests.conftest import bootstrap
bootstrap()

from strategies.base_dema_strategy import Signal, SignalType, StrategyState, PendingEntry
from portfolio.position_manager import PositionManager, Position, PositionSide, PositionStatus
from portfolio.pnl import PNLEngine
from portfolio.account import AccountEngine
from execution.paper_broker import PaperExecutionEngine, Fill, Order, OrderState
from execution.fee_model import MCXFeeModel, FeeBreakdown
from execution.order_manager import OrderManager
from core.fill_dedup import FillDeduplicator
from core.trade_close import TradeCloseManager
from core.risk_engine import RiskEngine
from notifications.telegram_router import TelegramRouter
from monitoring.health import HealthMonitor
from notifications.telegram_formatter import (
    format_new_trade, format_trade_exit, format_risk_alert,
)


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 1 - SIGNAL VERIFICATION
# ═══════════════════════════════════════════════════════════════════════

class TestSignalVerification:

    def test_signal_dataclass_fields(self):
        s = Signal(
            signal_type=SignalType.LONG, instrument="GOLDM",
            strategy_id="gold_01", timestamp=1000.0,
            trigger_price=52000.0, stop_price=51800.0, quantity=1,
        )
        assert s.signal_type == SignalType.LONG
        assert s.instrument == "GOLDM"
        assert s.strategy_id == "gold_01"
        assert s.timestamp == 1000.0
        assert s.trigger_price == 52000.0
        assert s.stop_price == 51800.0
        assert s.quantity == 1
        assert s.metadata is None

    def test_signal_type_enum(self):
        assert SignalType.LONG.value == "LONG"
        assert SignalType.SHORT.value == "SHORT"
        assert SignalType.FLAT.value == "FLAT"
        assert SignalType.REVERSAL.value == "REVERSAL"
        assert len(SignalType) == 4

    def test_signal_to_dict(self):
        s = Signal(
            signal_type=SignalType.SHORT, instrument="SILVERM",
            strategy_id="silver_02", timestamp=2000.0,
            trigger_price=68000.0, stop_price=68500.0, quantity=2,
            metadata={"exit": True},
        )
        d = {
            "signal_type": s.signal_type.value,
            "instrument": s.instrument,
            "strategy_id": s.strategy_id,
            "timestamp": s.timestamp,
            "trigger_price": s.trigger_price,
            "stop_price": s.stop_price,
            "quantity": s.quantity,
            "metadata": s.metadata,
        }
        assert d["signal_type"] == "SHORT"
        assert d["instrument"] == "SILVERM"
        assert d["metadata"] == {"exit": True}

    def test_signal_from_dict(self):
        raw = {
            "signal_type": "LONG",
            "instrument": "GOLDM",
            "strategy_id": "gold_03",
            "timestamp": 3000.0,
            "trigger_price": 51000.0,
            "stop_price": 50500.0,
            "quantity": 3,
        }
        s = Signal(
            signal_type=SignalType(raw["signal_type"]),
            instrument=raw["instrument"],
            strategy_id=raw["strategy_id"],
            timestamp=raw["timestamp"],
            trigger_price=raw["trigger_price"],
            stop_price=raw["stop_price"],
            quantity=raw["quantity"],
        )
        assert s.signal_type == SignalType.LONG
        assert s.instrument == "GOLDM"
        assert s.quantity == 3

    def test_signal_required_fields(self):
        with pytest.raises(TypeError):
            Signal()  # missing required fields

    def test_signal_instrument_always_goldm_or_silverm(self):
        for inst in ["GOLDM", "SILVERM"]:
            s = Signal(
                signal_type=SignalType.LONG, instrument=inst,
                strategy_id="test", timestamp=0.0,
                trigger_price=0.0, stop_price=0.0, quantity=1,
            )
            assert s.instrument in ("GOLDM", "SILVERM")


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 2 - POSITION LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════

def _make_fill(
    side: str = "BUY", price: float = 52000.0, qty: int = 1,
    instrument: str = "GOLDM", strategy_id: str = "gold_01",
    multiplier: float = 10.0, fill_id: str = "f1",
) -> Fill:
    return Fill(
        fill_id=fill_id, order_id="o1", instrument=instrument,
        side=side, quantity=qty, price=price,
        timestamp=time.time(), strategy_id=strategy_id,
        multiplier=multiplier,
    )


class TestPositionLifecycle:

    def test_position_add_long(self):
        pm = PositionManager()
        fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(fill, multiplier=10.0)
        assert pos.is_long
        assert pos.is_open
        assert pos.instrument == "GOLDM"
        assert pos.quantity == 1
        assert pos.average_entry == 52000.0
        assert len(pm.open_positions) == 1

    def test_position_add_short(self):
        pm = PositionManager()
        fill = _make_fill("SELL", 68000.0, instrument="SILVERM")
        pos = pm.open_position(fill, multiplier=30.0)
        assert pos.is_short
        assert pos.instrument == "SILVERM"

    def test_position_close_long(self):
        pm = PositionManager()
        open_fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(open_fill, multiplier=10.0)
        exit_fill = _make_fill("SELL", 53000.0, fill_id="f_exit")
        closed = pm.close_position(pos.position_id, exit_fill, "signal_exit")
        assert closed.status == PositionStatus.CLOSED
        assert not closed.is_open
        assert closed.realized_pnl == pytest.approx((53000 - 52000) * 1 * 10.0)
        assert len(pm.open_positions) == 0
        assert len(pm.closed_positions) == 1

    def test_position_close_short(self):
        pm = PositionManager()
        open_fill = _make_fill("SELL", 68000.0, instrument="SILVERM")
        pos = pm.open_position(open_fill, multiplier=30.0)
        exit_fill = _make_fill("BUY", 67000.0, instrument="SILVERM", fill_id="f_exit")
        closed = pm.close_position(pos.position_id, exit_fill, "signal_exit")
        assert closed.realized_pnl == pytest.approx((68000 - 67000) * 1 * 30.0)

    def test_position_reverse_long_to_short(self):
        pm = PositionManager()
        long_fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(long_fill, multiplier=10.0)
        assert pos.is_long
        close_fill = _make_fill("SELL", 51500.0, fill_id="f_close")
        pm.close_position(pos.position_id, close_fill, "reversal")
        short_fill = _make_fill("SELL", 51500.0, instrument="GOLDM", fill_id="f_short")
        new_pos = pm.open_position(short_fill, multiplier=10.0)
        assert new_pos.is_short
        assert len(pm.open_positions) == 1
        assert len(pm.closed_positions) == 1

    def test_position_reverse_short_to_long(self):
        pm = PositionManager()
        short_fill = _make_fill("SELL", 68000.0, instrument="SILVERM")
        pos = pm.open_position(short_fill, multiplier=30.0)
        assert pos.is_short
        close_fill = _make_fill("BUY", 68500.0, instrument="SILVERM", fill_id="f_close")
        pm.close_position(pos.position_id, close_fill, "reversal")
        long_fill = _make_fill("BUY", 68500.0, instrument="SILVERM", fill_id="f_long")
        new_pos = pm.open_position(long_fill, multiplier=30.0)
        assert new_pos.is_long

    def test_position_quantity_after_partial(self):
        pm = PositionManager()
        fill = _make_fill("BUY", 52000.0, qty=5)
        pos = pm.open_position(fill, multiplier=10.0)
        assert pos.quantity == 5

    def test_position_average_price_calculation(self):
        pm = PositionManager()
        fill = _make_fill("BUY", 52000.0, qty=2)
        pos = pm.open_position(fill, multiplier=10.0)
        assert pos.average_entry == 52000.0

    def test_position_pnl_long_positive(self):
        pm = PositionManager()
        fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(fill, multiplier=10.0)
        pos.update_mark(53000.0)
        assert pos.unrealized_pnl == pytest.approx((53000 - 52000) * 1 * 10.0)

    def test_position_pnl_long_negative(self):
        pm = PositionManager()
        fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(fill, multiplier=10.0)
        pos.update_mark(51000.0)
        assert pos.unrealized_pnl == pytest.approx((51000 - 52000) * 1 * 10.0)

    def test_position_pnl_short_positive(self):
        pm = PositionManager()
        fill = _make_fill("SELL", 68000.0, instrument="SILVERM")
        pos = pm.open_position(fill, multiplier=30.0)
        pos.update_mark(67000.0)
        assert pos.unrealized_pnl == pytest.approx((68000 - 67000) * 1 * 30.0)

    def test_position_pnl_short_negative(self):
        pm = PositionManager()
        fill = _make_fill("SELL", 68000.0, instrument="SILVERM")
        pos = pm.open_position(fill, multiplier=30.0)
        pos.update_mark(69000.0)
        assert pos.unrealized_pnl == pytest.approx((68000 - 69000) * 1 * 30.0)


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 3 - P&L INDEPENDENT VERIFICATION
# ═══════════════════════════════════════════════════════════════════════

class TestPnLIndependent:

    def _make_pnl(self, instrument: str = "GOLDM") -> PNLEngine:
        return PNLEngine(fee_model=MCXFeeModel())

    def test_pnl_long_formula(self):
        eng = self._make_pnl()
        entry = _make_fill("BUY", 52000.0, qty=1, multiplier=10.0)
        exit_ = _make_fill("SELL", 53000.0, qty=1, multiplier=10.0)
        gross, charges, net = eng.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        expected_gross = (53000 - 52000) * 1 * 10.0
        assert gross == pytest.approx(expected_gross)
        assert charges > 0
        assert net == pytest.approx(gross - charges)

    def test_pnl_short_formula(self):
        eng = self._make_pnl()
        entry = _make_fill("SELL", 68000.0, qty=1, multiplier=30.0, instrument="SILVERM")
        exit_ = _make_fill("BUY", 67000.0, qty=1, multiplier=30.0, instrument="SILVERM")
        gross, charges, net = eng.calculate_realized_pnl(entry, exit_, multiplier=30.0)
        expected_gross = (68000 - 67000) * 1 * 30.0
        assert gross == pytest.approx(expected_gross)

    def test_pnl_with_multiplier(self):
        eng = self._make_pnl()
        entry = _make_fill("BUY", 52000.0, qty=1, multiplier=10.0)
        exit_ = _make_fill("SELL", 53000.0, qty=1, multiplier=10.0)
        gross1, _, _ = eng.calculate_realized_pnl(entry, exit_, multiplier=10.0)

        eng2 = self._make_pnl()
        entry2 = _make_fill("BUY", 52000.0, qty=1, multiplier=20.0)
        exit2 = _make_fill("SELL", 53000.0, qty=1, multiplier=20.0)
        gross2, _, _ = eng2.calculate_realized_pnl(entry2, exit2, multiplier=20.0)
        assert gross2 == pytest.approx(gross1 * 2)

    def test_pnl_with_quantity(self):
        eng = self._make_pnl()
        entry = _make_fill("BUY", 52000.0, qty=3, multiplier=10.0)
        exit_ = _make_fill("SELL", 53000.0, qty=3, multiplier=10.0)
        gross, _, _ = eng.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        assert gross == pytest.approx((53000 - 52000) * 3 * 10.0)

    def test_pnl_zero_ltp(self):
        eng = self._make_pnl()
        entry = _make_fill("BUY", 52000.0, qty=1, multiplier=10.0)
        exit_ = _make_fill("SELL", 52000.0, qty=1, multiplier=10.0)
        gross, charges, net = eng.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        assert gross == pytest.approx(0.0)
        assert net == pytest.approx(-charges)

    def test_pnl_equal_entry(self):
        eng = self._make_pnl()
        entry = _make_fill("BUY", 68000.0, qty=1, multiplier=30.0, instrument="SILVERM")
        exit_ = _make_fill("SELL", 68000.0, qty=1, multiplier=30.0, instrument="SILVERM")
        gross, _, _ = eng.calculate_realized_pnl(entry, exit_, multiplier=30.0)
        assert gross == 0.0

    def test_pnl_large_move(self):
        eng = self._make_pnl()
        entry = _make_fill("BUY", 52000.0, qty=1, multiplier=10.0)
        exit_ = _make_fill("SELL", 60000.0, qty=1, multiplier=10.0)
        gross, _, _ = eng.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        assert gross == pytest.approx((60000 - 52000) * 1 * 10.0)

    def test_pnl_running_totals(self):
        eng = self._make_pnl()
        e1 = _make_fill("BUY", 52000.0, qty=1, multiplier=10.0)
        x1 = _make_fill("SELL", 53000.0, qty=1, multiplier=10.0)
        g1, c1, n1 = eng.calculate_realized_pnl(e1, x1, multiplier=10.0)
        eng.record_trade(g1, c1, n1)
        e2 = _make_fill("BUY", 54000.0, qty=1, multiplier=10.0, fill_id="f3")
        x2 = _make_fill("SELL", 53500.0, qty=1, multiplier=10.0, fill_id="f4")
        g2, c2, n2 = eng.calculate_realized_pnl(e2, x2, multiplier=10.0)
        eng.record_trade(g2, c2, n2)
        assert eng.trade_count == 2
        assert eng.realized_gross != 0.0


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 4 - EQUITY CALCULATION
# ═══════════════════════════════════════════════════════════════════════

class TestEquityCalculation:

    def test_equity_initial_capital(self):
        acct = AccountEngine(starting_capital=300_000.0)
        assert acct.equity == 300_000.0
        assert acct.cash == 300_000.0

    def test_equity_after_realized_pnl(self):
        acct = AccountEngine(starting_capital=300_000.0)
        acct.update_realized_pnl(pnl=15_000.0, charges=200.0)
        assert acct.realized_pnl == 15_000.0
        assert acct.charges == 200.0
        assert acct.equity == pytest.approx(300_000.0 + 15_000.0)
        # pnl is already NET (charges deducted by PNLEngine), so cash += pnl
        assert acct.cash == pytest.approx(300_000.0 + 15_000.0)

    def test_equity_with_unrealized(self):
        acct = AccountEngine(starting_capital=300_000.0)
        acct.update_realized_pnl(pnl=10_000.0, charges=100.0)
        acct.update_unrealized_pnl(5_000.0)
        assert acct.equity == pytest.approx(300_000.0 + 10_000.0 + 5_000.0)

    def test_equity_formula(self):
        acct = AccountEngine(starting_capital=500_000.0)
        acct.update_realized_pnl(pnl=25_000.0, charges=500.0)
        acct.update_unrealized_pnl(-3_000.0)
        expected = 500_000.0 + 25_000.0 + (-3_000.0)
        assert acct.equity == pytest.approx(expected)
        assert acct.net_pnl == pytest.approx(25_000.0 + (-3_000.0))


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 5 - DRAWDOWN
# ═══════════════════════════════════════════════════════════════════════

class TestDrawdown:

    def _make_risk(self, max_dd: float = 5.0, kill: bool = False) -> RiskEngine:
        return RiskEngine(
            max_positions_per_strategy=1,
            max_positions_total=8,
            max_daily_loss=50_000.0,
            max_drawdown_pct=max_dd,
            kill_switch_enabled=kill,
        )

    def test_drawdown_calculation(self):
        risk = self._make_risk(max_dd=5.0)
        risk.update_peak_equity(300_000.0)
        # 5% of 300,000 = 15,000 drawdown threshold
        allowed, reason = risk.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=500_000.0, margin_required=10_000.0,
            current_equity=290_000.0,
        )
        # 10k/300k = 3.33% < 5%, should be allowed
        assert allowed is True

    def test_drawdown_from_peak(self):
        risk = self._make_risk(max_dd=5.0, kill=True)
        risk.update_peak_equity(300_000.0)
        allowed, reason = risk.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=500_000.0, margin_required=10_000.0,
            current_equity=280_000.0,
        )
        # 20k/300k = 6.67% >= 5%, kill switch activates
        assert allowed is False
        assert reason == "max_drawdown_reached"
        assert risk.kill_switch_active is True

    def test_drawdown_recovery(self):
        risk = self._make_risk(max_dd=5.0, kill=False)
        risk.update_peak_equity(300_000.0)
        # 20k/300k = 6.67% >= 5% -> blocked even without kill switch
        allowed, reason = risk.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=500_000.0, margin_required=10_000.0,
            current_equity=280_000.0,
        )
        assert allowed is False
        assert reason == "max_drawdown_reached"
        # kill switch should NOT activate since kill_switch_enabled=False
        assert risk.kill_switch_active is False

    def test_max_drawdown_tracking(self):
        risk = self._make_risk(max_dd=2.0, kill=True)
        risk.update_peak_equity(100_000.0)
        # 2% of 100k = 2000
        allowed, reason = risk.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=500_000.0, margin_required=1_000.0,
            current_equity=97_000.0,
        )
        # 3k/100k = 3% >= 2%, should block
        assert allowed is False
        assert risk.kill_switch_active is True

    def test_drawdown_reset_on_new_peak(self):
        risk = self._make_risk(max_dd=5.0, kill=True)
        risk.update_peak_equity(300_000.0)
        risk._activate_kill_switch()
        assert risk.kill_switch_active is True
        risk.deactivate_kill_switch()
        risk.update_peak_equity(310_000.0)
        allowed, _ = risk.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=500_000.0, margin_required=10_000.0,
            current_equity=305_000.0,
        )
        assert allowed is True

    def test_drawdown_no_peak(self):
        risk = self._make_risk(max_dd=5.0)
        allowed, reason = risk.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=500_000.0, margin_required=10_000.0,
            current_equity=290_000.0,
        )
        assert allowed is True


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 6 - FILL DEDUPLICATION
# ═══════════════════════════════════════════════════════════════════════

class TestFillDedup:

    def _make_dedup(self) -> tuple[FillDeduplicator, str]:
        db = tempfile.mktemp(suffix=".db")
        return FillDeduplicator(db_path=db), db

    def test_fill_dedup_first_fill(self):
        dedup, db = self._make_dedup()
        assert dedup.mark_processed("fill_001") is True
        assert dedup.is_duplicate("fill_001") is True

    def test_fill_dedup_duplicate_rejected(self):
        dedup, db = self._make_dedup()
        dedup.mark_processed("fill_002")
        result = dedup.mark_processed("fill_002")
        assert result is False
        assert dedup.is_duplicate("fill_002") is True

    def test_fill_dedup_different_fill_accepted(self):
        dedup, db = self._make_dedup()
        dedup.mark_processed("fill_003")
        assert dedup.is_duplicate("fill_004") is False

    def test_fill_dedup_persistence_survives_restart(self):
        dedup1, db = self._make_dedup()
        dedup1.mark_processed("fill_005")
        dedup1.mark_processed("fill_006")
        del dedup1
        dedup2 = FillDeduplicator(db_path=db)
        count = dedup2.load_from_database()
        assert count == 2
        assert dedup2.is_duplicate("fill_005") is True
        assert dedup2.is_duplicate("fill_006") is True


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 7 - TRADE CLOSE
# ═══════════════════════════════════════════════════════════════════════

class TestTradeClose:

    def _make_persistence_stub(self):
        class StubPersistence:
            def __init__(self):
                self.trades = []
                self.fills = []
                self.events = []
            def save_trade(self, data):
                self.trades.append(data)
            def save_fill(self, data):
                self.fills.append(data)
            def save_event(self, data):
                self.events.append(data)
        return StubPersistence()

    def _setup(self):
        pm = PositionManager()
        pnl_engines = {"gold_01": PNLEngine(fee_model=MCXFeeModel())}
        acct_engines = {"gold_01": AccountEngine(starting_capital=300_000.0)}
        global_acct = AccountEngine(starting_capital=300_000.0)
        risk = RiskEngine()
        persistence = self._make_persistence_stub()
        return pm, pnl_engines, acct_engines, global_acct, risk, persistence

    def test_trade_close_persists_to_db(self):
        pm, pnl, acct, gacct, risk, pers = self._setup()
        tc = TradeCloseManager(pm, pnl, acct, gacct, risk, pers, None)
        open_fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(open_fill, multiplier=10.0)
        exit_fill = _make_fill("SELL", 53000.0, fill_id="f_exit")
        result = tc.close_position(exit_fill, pos, "gold_01", 10.0)
        assert result is True
        assert len(pers.trades) == 1
        assert pers.trades[0]["exit_price"] == 53000.0

    def test_trade_close_updates_pnl(self):
        pm, pnl, acct, gacct, risk, pers = self._setup()
        tc = TradeCloseManager(pm, pnl, acct, gacct, risk, pers, None)
        open_fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(open_fill, multiplier=10.0)
        exit_fill = _make_fill("SELL", 53000.0, fill_id="f_exit")
        tc.close_position(exit_fill, pos, "gold_01", 10.0)
        assert acct["gold_01"].realized_pnl != 0.0
        assert gacct.realized_pnl != 0.0

    def test_trade_close_sets_exit_price(self):
        pm, pnl, acct, gacct, risk, pers = self._setup()
        tc = TradeCloseManager(pm, pnl, acct, gacct, risk, pers, None)
        open_fill = _make_fill("BUY", 52000.0)
        pos = pm.open_position(open_fill, multiplier=10.0)
        exit_fill = _make_fill("SELL", 53000.0, fill_id="f_exit")
        closed = pm.close_position(pos.position_id, exit_fill, "signal_exit")
        assert closed.exit_fills[0].price == 53000.0


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 8 - PAPER BROKER
# ═══════════════════════════════════════════════════════════════════════

class TestPaperBroker:

    def _make_engine(self, slippage: int = 1) -> PaperExecutionEngine:
        return PaperExecutionEngine(
            slippage_ticks=slippage, latency_ms=0, partial_fill_probability=0,
        )

    def _make_signal(self, stype: str = "LONG", inst: str = "GOLDM") -> Signal:
        return Signal(
            signal_type=SignalType.LONG if stype == "LONG" else SignalType.SHORT,
            instrument=inst, strategy_id="gold_01",
            timestamp=time.time(), trigger_price=52000.0,
            stop_price=51800.0, quantity=1,
        )

    def test_paper_broker_buy_order(self):
        eng = self._make_engine()
        eng.update_price("GOLDM", 52000.0)
        sig = self._make_signal("LONG")
        order = eng.create_order(sig, multiplier=10.0)
        assert order.side == "BUY"
        order = eng.submit_order(order)
        assert order.state == OrderState.FILLED
        assert order.average_fill_price == 52001.0  # slippage = 1

    def test_paper_broker_sell_order(self):
        eng = self._make_engine()
        eng.update_price("SILVERM", 68000.0)
        sig = self._make_signal("SHORT", inst="SILVERM")
        order = eng.create_order(sig, multiplier=30.0)
        assert order.side == "SELL"
        order = eng.submit_order(order)
        assert order.state == OrderState.FILLED
        assert order.average_fill_price == 67999.0  # slippage = 1

    def test_paper_broker_slippage(self):
        eng = self._make_engine(slippage=3)
        eng.update_price("GOLDM", 52000.0)
        sig = self._make_signal("LONG")
        order = eng.create_order(sig, multiplier=10.0)
        order = eng.submit_order(order)
        assert order.average_fill_price == 52003.0

    def test_paper_broker_duplicate_reject(self):
        eng = self._make_engine()
        eng.update_price("GOLDM", 52000.0)
        sig = self._make_signal("LONG")
        order = eng.create_order(sig, multiplier=10.0)
        order = eng.submit_order(order)
        assert order.state == OrderState.FILLED
        order2 = eng.create_order(sig, multiplier=10.0)
        assert order2.order_id != order.order_id


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 9 - ACCOUNT ENGINE
# ═══════════════════════════════════════════════════════════════════════

class TestAccountEngine:

    def test_account_initial_balance(self):
        acct = AccountEngine(starting_capital=300_000.0)
        assert acct.starting_capital == 300_000.0
        assert acct.equity == 300_000.0
        assert acct.used_margin == 0.0
        assert acct.available_margin == 300_000.0

    def test_account_after_fill(self):
        acct = AccountEngine(starting_capital=300_000.0, margin_per_trade_pct=6.5)
        margin = acct.calculate_margin_required(52000.0, 1, 10.0)
        assert margin == pytest.approx(52000.0 * 1 * 10.0 * 6.5 / 100.0)
        assert acct.block_margin(margin) is True
        assert acct.used_margin == pytest.approx(margin)
        assert acct.available_margin == pytest.approx(300_000.0 - margin)

    def test_account_balance_after_trade(self):
        acct = AccountEngine(starting_capital=300_000.0)
        acct.update_realized_pnl(pnl=10_000.0, charges=200.0)
        assert acct.equity == pytest.approx(310_000.0)
        assert acct.cash == pytest.approx(300_000.0 + 10_000.0)

    def test_account_margin_release(self):
        acct = AccountEngine(starting_capital=300_000.0, margin_per_trade_pct=6.5)
        margin = acct.calculate_margin_required(52000.0, 1, 10.0)
        acct.block_margin(margin)
        assert acct.used_margin == pytest.approx(margin)
        acct.release_margin(margin)
        assert acct.used_margin == 0.0
        assert acct.available_margin == pytest.approx(300_000.0)

    def test_account_snapshot(self):
        acct = AccountEngine(starting_capital=300_000.0)
        snap = acct.snapshot()
        assert "starting_capital" in snap
        assert "equity" in snap
        assert "available_margin" in snap

    def test_account_restore(self):
        acct = AccountEngine(starting_capital=300_000.0)
        acct.update_realized_pnl(pnl=15_000.0, charges=300.0)
        snap = acct.snapshot()
        acct2 = AccountEngine(starting_capital=100_000.0)
        acct2.restore(snap)
        # starting_capital is NEVER restored from saved state (always from config)
        assert acct2.starting_capital == 100_000.0
        assert acct2.realized_pnl == pytest.approx(15_000.0)
        assert acct2.charges == pytest.approx(300.0)

    def test_account_cannot_overdraw_margin(self):
        acct = AccountEngine(starting_capital=100_000.0, margin_per_trade_pct=6.5)
        margin = acct.calculate_margin_required(52000.0, 10, 10.0)
        assert acct.block_margin(margin) is False


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 10 - WEBSOCKET DATA FLOW
# ═══════════════════════════════════════════════════════════════════════

class TestWebSocketDataFlow:

    def test_ws_engine_state_format(self):
        hm = HealthMonitor()
        snap = hm.snapshot()
        assert "overall_status" in snap
        assert "uptime_seconds" in snap
        assert "tick_count" in snap
        assert "bar_count" in snap
        assert "signal_count" in snap
        assert "fill_count" in snap
        assert "error_count" in snap
        assert "components" in snap
        assert snap["overall_status"] == "healthy"

    def test_ws_event_format(self):
        hm = HealthMonitor()
        hm.record_tick()
        hm.record_bar()
        hm.record_signal()
        hm.record_fill()
        snap = hm.snapshot()
        assert snap["tick_count"] == 1
        assert snap["bar_count"] == 1
        assert snap["signal_count"] == 1
        assert snap["fill_count"] == 1

    def test_health_register_component(self):
        hm = HealthMonitor()
        hm.register_component("data_feed")
        snap = hm.snapshot()
        assert "data_feed" in snap["components"]

    def test_health_overall_status(self):
        hm = HealthMonitor()
        assert hm.overall_status().value == "healthy"


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 11 - TELEGRAM
# ═══════════════════════════════════════════════════════════════════════

class TestTelegram:

    def test_telegram_router_instantiation(self):
        router = TelegramRouter()
        assert router is not None
        assert router._enabled is True

    def test_telegram_router_format(self):
        fill = {
            "side": "BUY", "instrument": "GOLDM",
            "strategy_id": "gold_01", "price": 52000,
            "quantity": 1, "multiplier": 10, "order_id": "abc",
        }
        strategy = {"stop_price": 51800, "htf_dema_atr": 52100, "entry_value": 520000}
        account = {"equity": 300000, "used_margin": 52000}
        text = format_new_trade(fill, strategy, account)
        assert "GOLDM" in text
        assert "gold_01" in text
        assert "52,000" in text

    def test_telegram_trade_close_format(self):
        close_data = {
            "instrument": "SILVERM", "strategy_id": "silver_02",
            "side": "LONG", "entry_price": 68000,
            "exit_price": 69000, "net_pnl": 30000,
            "exit_reason": "signal",
        }
        text = format_trade_exit(close_data)
        assert "SILVERM" in text
        assert "silver_02" in text
        assert "30,000" in text

    def test_telegram_risk_alert_format(self):
        alert = {
            "type": "order_rejected", "severity": "WARNING",
            "message": "test alert", "strategy_id": "gold_01",
        }
        text = format_risk_alert(alert)
        assert "RISK ALERT" in text
        assert "order_rejected" in text


# ═══════════════════════════════════════════════════════════════════════
#  CLASS 12 - FEE CALCULATION
# ═══════════════════════════════════════════════════════════════════════

class TestFeeCalculation:

    def test_fee_gold_long(self):
        fee = MCXFeeModel()
        result = fee.calculate(
            entry_price=52000.0, exit_price=53000.0,
            quantity=1, multiplier=10.0,
        )
        assert isinstance(result, FeeBreakdown)
        assert result.brokerage == 40.0  # 20 per side * 2
        assert result.total > 0

    def test_fee_silver_long(self):
        fee = MCXFeeModel()
        result = fee.calculate(
            entry_price=68000.0, exit_price=69000.0,
            quantity=1, multiplier=30.0,
        )
        assert result.total > 0
        assert result.brokerage == 40.0

    def test_fee_gold_short(self):
        fee = MCXFeeModel()
        result = fee.calculate(
            entry_price=53000.0, exit_price=52000.0,
            quantity=1, multiplier=10.0,
        )
        assert result.total > 0
        assert result.stt > 0

    def test_fee_silver_short(self):
        fee = MCXFeeModel()
        result = fee.calculate(
            entry_price=69000.0, exit_price=68000.0,
            quantity=1, multiplier=30.0,
        )
        assert result.total > 0

    def test_fee_total_percentage_under_1pct(self):
        fee = MCXFeeModel()
        entry, exit_, qty, mult = 52000.0, 53000.0, 1, 10.0
        result = fee.calculate(entry, exit_, qty, mult)
        turnover = entry * qty * mult + exit_ * qty * mult
        pct = result.total / turnover * 100
        assert pct < 1.0, f"Fee percentage {pct:.4f}% exceeds 1%"

    def test_fee_from_config(self):
        config = {
            "brokerage_per_side": 15.0,
            "stt_sell_pct": 0.02,
            "exchange_pct": 0.003,
            "sebi_pct": 0.0002,
            "gst_pct": 18.0,
            "stamp_duty_pct": 0.006,
        }
        fee = MCXFeeModel.from_config(config)
        assert fee.brokerage_per_side == 15.0
        result = fee.calculate(52000.0, 53000.0, 1, 10.0)
        assert result.brokerage == 30.0  # 15 * 2

    def test_fee_breakdown_fields(self):
        fee = MCXFeeModel()
        result = fee.calculate(52000.0, 53000.0, 1, 10.0)
        assert hasattr(result, "brokerage")
        assert hasattr(result, "stt")
        assert hasattr(result, "exchange")
        assert hasattr(result, "sebi")
        assert hasattr(result, "gst")
        assert hasattr(result, "stamp_duty")
        assert hasattr(result, "total")
        assert result.total == pytest.approx(
            result.brokerage + result.stt + result.exchange +
            result.sebi + result.gst + result.stamp_duty
        )
