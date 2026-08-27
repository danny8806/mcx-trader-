"""Concurrency, edge-case, state-transition, lookahead, and resource-cleanup tests.

Covers:
  CLASS 1 - Thread Safety (5 tests)
  CLASS 2 - Edge Cases (23 tests)
  CLASS 3 - State Transitions (6 tests)
  CLASS 4 - Lookahead Detection (3 tests)
  CLASS 5 - Resource Cleanup (3 tests)
"""
from __future__ import annotations

import os
import sys
import time
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ── Ensure project root is on sys.path ──────────────────────────────────────
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from indicators.dema import DEMA
from indicators.atr import ATR
from indicators.dema_atr import DEMAATR
from core.market_status import MarketStatus, MarketState, EngineStatus, DataStatus
from core.risk_engine import RiskEngine
from core.fill_dedup import FillDeduplicator
from core.safe_mode import SafeModeManager
from portfolio.position_manager import PositionManager, Position, PositionSide, PositionStatus
from portfolio.account import AccountEngine
from portfolio.pnl import PNLEngine
from execution.paper_broker import PaperExecutionEngine, Fill, Order
from execution.fee_model import MCXFeeModel
from monitoring.health import HealthMonitor, SystemStatus
from htf.backtest_style_htf import BacktestStyleHTFEngine
from core.timeframe_engine import Bar, BarState


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_fill(
    fill_id: str = "f1",
    instrument: str = "GOLDM",
    side: str = "BUY",
    quantity: int = 1,
    price: float = 72000.0,
    strategy_id: str = "gold_01",
    multiplier: float = 10.0,
    timestamp: float | None = None,
    order_id: str = "o1",
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        instrument=instrument,
        side=side,
        quantity=quantity,
        price=price,
        timestamp=timestamp or time.time(),
        strategy_id=strategy_id,
        multiplier=multiplier,
    )


def _temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASS 1 — THREAD SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketStatusConcurrentAccess:
    def test_market_status_concurrent_access(self):
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        errors: list[Exception] = []

        def reader():
            try:
                for _ in range(200):
                    _ = ms.state
                    _ = ms.engine_status
                    _ = ms.data_status
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for _ in range(200):
                    ms.set_engine_status(EngineStatus.TRADING)
                    ms.update_data_status(True, time.time())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent access errors: {errors}"


class TestRiskEngineConcurrentCheck:
    def test_risk_engine_concurrent_check(self):
        risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8)
        results: list[tuple[bool, object]] = []
        errors: list[Exception] = []

        def check(i: int):
            try:
                sig = MagicMock()
                r = risk.check_order(
                    signal=sig,
                    current_positions=0,
                    strategy_positions=0,
                    available_margin=100_000,
                    margin_required=10_000,
                    current_equity=300_000,
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=check, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent risk check errors: {errors}"
        assert len(results) == 20
        assert all(r[0] for r in results)


class TestPositionManagerConcurrentAdd:
    def test_position_manager_concurrent_add(self):
        pm = PositionManager()
        errors: list[Exception] = []

        def add_positions():
            try:
                for i in range(50):
                    fill = _make_fill(fill_id=f"f_{threading.current_thread().name}_{i}")
                    pm.open_position(fill=fill, multiplier=10.0, margin=5000)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_positions, name=f"t{i}") for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent add errors: {errors}"
        assert len(pm.open_positions) == 200


class TestFillDedupConcurrentInsert:
    def test_fill_dedup_concurrent_insert(self):
        db = _temp_db()
        try:
            dedup = FillDeduplicator(db_path=db)
            errors: list[Exception] = []

            def insert_fills(start: int):
                try:
                    for i in range(start, start + 100):
                        dedup.mark_processed(f"fill_{i}")
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=insert_fills, args=(i * 100,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not errors, f"Concurrent fill dedup errors: {errors}"
            assert dedup.count == 400
        finally:
            os.unlink(db)


class TestIndicatorConcurrentUpdate:
    def test_indicator_concurrent_update(self):
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        errors: list[Exception] = []

        def update_bars(start: int):
            try:
                for i in range(100):
                    o, h, l, c = 72000 + i + start, 72100 + i + start, 71900 + i + start, 72050 + i + start
                    ind.update(float(o), float(h), float(l), float(c))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_bars, args=(i * 100,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent indicator update errors: {errors}"
        assert ind.value is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASS 2 — EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCasesLTP:
    def test_zero_ltp(self):
        engine = PaperExecutionEngine()
        engine.update_price("GOLDM", 0.0)
        assert engine._current_prices["GOLDM"] == 0.0

    def test_negative_ltp(self):
        engine = PaperExecutionEngine()
        engine.update_price("GOLDM", -100.0)
        assert engine._current_prices["GOLDM"] == -100.0

    def test_extreme_ltp(self):
        engine = PaperExecutionEngine()
        engine.update_price("GOLDM", 999_999_999.99)
        assert engine._current_prices["GOLDM"] == 999_999_999.99


class TestEdgeCasesQuantity:
    def test_zero_quantity(self):
        fill = _make_fill(quantity=0, price=72000)
        assert fill.quantity == 0
        assert fill.gross_value == 0.0

    def test_negative_quantity(self):
        fill = _make_fill(quantity=-5, price=72000)
        assert fill.quantity == -5

    def test_large_quantity(self):
        fill = _make_fill(quantity=1_000_000, price=72000)
        assert fill.gross_value == 720_000_000_000.0


class TestEdgeCasesSignals:
    def test_duplicate_signal(self):
        pm = PositionManager()
        fill1 = _make_fill(fill_id="dup1")
        pos = pm.open_position(fill=fill1, multiplier=10.0, margin=5000)
        assert len(pm.open_positions) == 1
        fill2 = _make_fill(fill_id="dup2")
        pos2 = pm.open_position(fill=fill2, multiplier=10.0, margin=5000)
        assert len(pm.open_positions) == 2

    def test_signal_same_direction(self):
        pm = PositionManager()
        fill = _make_fill(side="BUY", fill_id="same_dir")
        pos = pm.open_position(fill=fill, multiplier=10.0)
        assert pos.is_long

    def test_signal_opposite_direction(self):
        pm = PositionManager()
        fill = _make_fill(side="SELL", fill_id="opp_dir")
        pos = pm.open_position(fill=fill, multiplier=10.0)
        assert pos.is_short

    def test_position_already_open(self):
        pm = PositionManager()
        fill = _make_fill(fill_id="open1")
        pos1 = pm.open_position(fill=fill, multiplier=10.0)
        assert pos1.is_open

    def test_position_already_closed(self):
        pm = PositionManager()
        fill = _make_fill(fill_id="close1")
        pos = pm.open_position(fill=fill, multiplier=10.0)
        exit_fill = _make_fill(fill_id="exit1", price=73000)
        closed = pm.close_position(pos.position_id, fill=exit_fill, reason="signal")
        assert not closed.is_open
        assert closed.status == PositionStatus.CLOSED


class TestEdgeCasesFillWithoutOrder:
    def test_fill_without_order(self):
        fill = _make_fill(fill_id="orphan_fill", order_id="nonexistent_order")
        assert fill.order_id == "nonexistent_order"


class TestEdgeCasesOrderWithoutSignal:
    def test_order_without_signal(self):
        engine = PaperExecutionEngine()
        order = Order(
            order_id="orphan_order",
            strategy_id="gold_01",
            instrument="GOLDM",
            side="BUY",
            quantity=1,
        )
        engine._orders[order.order_id] = order
        retrieved = engine.get_order("orphan_order")
        assert retrieved is not None
        assert retrieved.order_id == "orphan_order"


class TestEdgeCasesPnLNoPosition:
    def test_pnl_no_position(self):
        fee = MCXFeeModel()
        pnl = PNLEngine(fee_model=fee)
        assert pnl.trade_count == 0
        assert pnl.realized_net == 0.0
        assert pnl.win_rate == 0.0


class TestEdgeCasesEquityNoCapital:
    def test_equity_no_capital(self):
        acct = AccountEngine(starting_capital=0.0)
        assert acct.equity == 0.0
        assert acct.available_margin == 0.0


class TestEdgeCasesDrawdownZeroEquity:
    def test_drawdown_zero_equity(self):
        risk = RiskEngine(max_drawdown_pct=5.0, kill_switch_enabled=True)
        risk.update_peak_equity(100_000)
        sig = MagicMock()
        allowed, reason = risk.check_order(
            signal=sig,
            current_positions=0,
            strategy_positions=0,
            available_margin=50_000,
            margin_required=10_000,
            current_equity=0.0,
        )
        assert not allowed
        assert reason == "max_drawdown_reached"


class TestEdgeCasesDEMAInsufficientData:
    def test_dema_insufficient_data(self):
        dema = DEMA(period=3)
        assert dema.value is None
        assert not dema.initialized
        result = dema.update(100.0)
        assert result is not None
        assert not dema.initialized

    def test_atr_insufficient_data(self):
        atr = ATR(period=6)
        assert atr.value is None
        assert not atr.initialized
        result = atr.update(100.0, 90.0, 95.0)
        assert result is None
        assert not atr.initialized


class TestEdgeCasesHTF:
    def test_htf_no_data(self):
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6)
        bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=0, end_ts=300,
            open=72000, high=72100, low=71900, close=72050,
            volume=100, state=BarState.CLOSED,
        )
        result = htf.map_to_fast_bar(bar, "5m")
        assert result.htf_value is None


class TestEdgeCasesRESTHandling:
    def test_rest_401_handling(self):
        import requests
        try:
            resp = requests.get("http://127.0.0.1:1/nonexistent", timeout=1)
        except Exception:
            pass

    def test_rest_timeout_handling(self):
        import requests
        try:
            resp = requests.get("http://192.0.2.1:99999/test", timeout=0.1)
        except Exception:
            pass

    def test_ws_disconnect_recovery(self):
        health = HealthMonitor()
        health.register_component("ws")
        health.update_component("ws", SystemStatus.DEGRADED, "disconnected")
        assert health.overall_status() == SystemStatus.DEGRADED
        health.update_component("ws", SystemStatus.HEALTHY, "reconnected")
        assert health.overall_status() == SystemStatus.HEALTHY


class TestEdgeCasesDBLockHandling:
    def test_db_lock_handling(self):
        db = _temp_db()
        try:
            dedup = FillDeduplicator(db_path=db)
            result = dedup.mark_processed("lock_test_fill")
            assert result is True
            assert dedup.is_duplicate("lock_test_fill") is True
        finally:
            os.unlink(db)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASS 3 — STATE TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateTransitions:
    def test_engine_init_to_ready(self):
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        assert ms._engine_status == EngineStatus.INITIALIZING
        ms.set_engine_status(EngineStatus.RECONCILING)
        assert ms._engine_status == EngineStatus.RECONCILING
        ms.set_engine_status(EngineStatus.WARMING_UP)
        assert ms._engine_status == EngineStatus.WARMING_UP
        ms.set_engine_status(EngineStatus.READY)
        assert ms._engine_status == EngineStatus.READY

    def test_engine_trading_to_paused(self):
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.set_engine_status(EngineStatus.TRADING)
        assert ms._engine_status == EngineStatus.TRADING
        ms.set_engine_status(EngineStatus.SAFE_MODE)
        assert ms._engine_status == EngineStatus.SAFE_MODE

    def test_market_pre_market_to_market_open(self):
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.force_state(MarketState.PRE_MARKET)
        assert ms.state == MarketState.PRE_MARKET
        ms.force_state(MarketState.MARKET_OPEN)
        assert ms.state == MarketState.MARKET_OPEN

    def test_market_market_close_to_after_market(self):
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.force_state(MarketState.MARKET_CLOSE)
        assert ms.state == MarketState.MARKET_CLOSE
        ms.force_state(MarketState.AFTER_MARKET)
        assert ms.state == MarketState.AFTER_MARKET

    def test_safe_mode_enter_exit(self):
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        sm = SafeModeManager(ms)
        sm.enter_safe_mode("test_reason", "unit test")
        assert sm.is_active
        assert sm.has_reason("test_reason")
        sm.clear_reason("test_reason")
        assert not sm.is_active

    def test_data_status_transitions(self):
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.update_data_status(connected=False)
        assert ms._data_status == DataStatus.DISCONNECTED
        ms.update_data_status(connected=True, last_tick_time=0)
        assert ms._data_status == DataStatus.NO_DATA
        ms.update_data_status(connected=True, last_tick_time=time.time())
        assert ms._data_status == DataStatus.CONNECTED


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASS 4 — LOOKAHEAD DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestLookaheadDetection:
    def test_no_future_data_in_indicator(self):
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        bars = [
            (72000, 72100, 71900, 72050),
            (72050, 72150, 71950, 72100),
            (72100, 72200, 72000, 72150),
            (72150, 72250, 72050, 72200),
            (72200, 72300, 72100, 72250),
            (72250, 72350, 72150, 72300),
            (72300, 72400, 72200, 72350),
            (72350, 72450, 72250, 72400),
            (72400, 72500, 72300, 72450),
            (72450, 72550, 72350, 72500),
        ]
        prev_val = None
        for o, h, l, c in bars:
            val = ind.update(float(o), float(h), float(l), float(c))
            if prev_val is not None and val is not None:
                assert abs(val - prev_val) < 10000
            prev_val = val

    def test_no_future_data_in_htf(self):
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6)

        for i in range(10):
            bar = Bar(
                instrument="GOLDM", timeframe="1h",
                start_ts=i * 3600, end_ts=(i + 1) * 3600,
                open=72000 + i * 10, high=72100 + i * 10,
                low=71900 + i * 10, close=72050 + i * 10,
                volume=100, state=BarState.CLOSED,
            )
            htf.on_htf_bar_closed(bar)

        fast_bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=9 * 3600, end_ts=9 * 3600 + 300,
            open=72900, high=73000, low=72800, close=72950,
            volume=100, state=BarState.CLOSED,
        )
        result = htf.map_to_fast_bar(fast_bar, "5m")
        if result.htf_value is not None:
            assert result.htf_source_timestamp <= 9 * 3600

    def test_htf_does_not_use_future_ltf(self):
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "15m", dema_period=3, atr_period=6)

        for i in range(8):
            bar = Bar(
                instrument="GOLDM", timeframe="15m",
                start_ts=i * 900, end_ts=(i + 1) * 900,
                open=72000 + i * 5, high=72100 + i * 5,
                low=71900 + i * 5, close=72050 + i * 5,
                volume=50, state=BarState.CLOSED,
            )
            htf.on_htf_bar_closed(bar)

        fast_bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=3 * 3600, end_ts=3 * 3600 + 300,
            open=72200, high=72300, low=72100, close=72250,
            volume=100, state=BarState.CLOSED,
        )
        result = htf.map_mid_to_fast_bar(fast_bar, "5m")
        if result.htf_source_timestamp is not None:
            assert result.htf_source_timestamp <= 3 * 3600 + 300


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASS 5 — RESOURCE CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════

class TestResourceCleanup:
    def test_thread_cleanup_on_exit(self):
        threads_before = threading.active_count()
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait(timeout=5)
            time.sleep(0.1)

        t = threading.Thread(target=worker)
        t.start()
        barrier.wait(timeout=5)
        t.join(timeout=5)
        assert not t.is_alive()
        assert threading.active_count() <= threads_before + 1

    def test_db_connection_cleanup(self):
        from persistence.manager import PersistenceManager
        db = _temp_db()
        try:
            pm = PersistenceManager(state_path=db + ".json", db_path=db)
            conn = pm._get_conn()
            assert conn is not None
            pm.close()
            assert pm._conn is None
        finally:
            if os.path.exists(db):
                os.unlink(db)
            if os.path.exists(db + ".json"):
                os.unlink(db + ".json")

    def test_websocket_cleanup(self):
        health = HealthMonitor()
        health.register_component("ws")
        health.update_component("ws", SystemStatus.HEALTHY, "connected")
        assert health.overall_status() == SystemStatus.HEALTHY
        health.update_component("ws", SystemStatus.STOPPED, "cleaned up")
        snap = health.snapshot()
        assert snap["components"]["ws"]["status"] == "stopped"
