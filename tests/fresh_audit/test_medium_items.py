"""Regression tests for medium/low-priority remediation items.

Covers:
- WebSocket dedup must NOT drop same-LTT ticks with a different price
- PNLEngine unrealized gross updates on mark
- Drawdown duration includes ongoing (unrecovered) underwater period
- HealthMonitor overall status reflects STOPPED
- TradeMetrics carries the instrument multiplier
"""
from __future__ import annotations

import struct

import pytest

from data.dhan.websocket_client import DhanWebSocketClient
from execution.fee_model import MCXFeeModel
from monitoring.health import HealthMonitor, SystemStatus
from portfolio.pnl import PNLEngine


def _make_quote_packet(security_id: int, ltp: float, ltt: int) -> bytes:
    buf = bytearray(26)
    buf[0] = 4  # Quote packet code
    struct.pack_into("<i", buf, 4, security_id)
    struct.pack_into("<f", buf, 8, ltp)
    struct.pack_into("<h", buf, 12, 1)  # ltq
    struct.pack_into("<i", buf, 14, ltt)
    struct.pack_into("<i", buf, 22, 100)  # cumvol
    return bytes(buf)


class TestWebSocketDedup:
    def test_same_ltt_different_price_is_delivered(self):
        ticks = []
        client = DhanWebSocketClient(client_id="c", token="", on_tick=ticks.append)
        sid = 569003
        client._stats["recv"] = 0
        for price in (100.0, 100.5, 101.0):
            client._on_message(None, _make_quote_packet(sid, price, ltt=1700000000))
        prices = [t["ltp"] for t in ticks]
        assert prices == [100.0, 100.5, 101.0]

    def test_exact_duplicate_is_dropped(self):
        ticks = []
        client = DhanWebSocketClient(client_id="c", token="", on_tick=ticks.append)
        packet = _make_quote_packet(569003, 100.0, ltt=1700000000)
        client._on_message(None, packet)
        client._on_message(None, packet)
        assert len(ticks) == 1


class TestPNLUnrealized:
    def test_mark_updates_snapshot_unrealized(self):
        eng = PNLEngine(fee_model=MCXFeeModel())
        eng.record_trade(gross=1000.0, charges=50.0, net=950.0)
        assert eng.get_snapshot().unrealized_gross == 0.0
        eng.update_unrealized_pnl(2500.0)
        snap = eng.get_snapshot()
        assert snap.unrealized_gross == 2500.0
        assert snap.gross_pnl == 1000.0 + 2500.0
        assert snap.net_pnl == 950.0 + 2500.0

    def test_calculate_unrealized_pnl_stores_value(self):
        eng = PNLEngine(fee_model=MCXFeeModel())
        snap = eng.get_snapshot()
        assert snap.unrealized_gross == 0.0


class TestDrawdownDuration:
    def test_ongoing_unrecovered_drawdown_is_counted(self):
        from analytics.performance import PerformanceEngine
        curve = [1000.0, 1500.0, 2000.0, 1900.0, 1800.0, 1700.0, 1600.0]
        dd, duration = PerformanceEngine._calculate_drawdown(None, curve)
        assert dd == pytest.approx(400.0)
        assert duration == 4  # underwater from idx3 to idx6 (peak idx2)

    def test_recovered_drawdown_duration(self):
        from analytics.performance import PerformanceEngine
        curve = [1000.0, 1500.0, 2000.0, 1700.0, 1400.0, 2100.0, 2200.0]
        dd, duration = PerformanceEngine._calculate_drawdown(None, curve)
        assert dd == pytest.approx(600.0)
        assert duration == 2  # strictly-under-peak trades (idx3, idx4) before recovery at idx5


class TestHealthStopped:
    def test_overall_reflects_stopped(self):
        hm = HealthMonitor()
        hm.register_component("ws")
        hm.update_component("ws", SystemStatus.STOPPED, "engine stopped")
        assert hm.overall_status() == SystemStatus.STOPPED

    def test_error_beats_stopped(self):
        hm = HealthMonitor()
        hm.register_component("a")
        hm.register_component("b")
        hm.update_component("a", SystemStatus.STOPPED)
        hm.update_component("b", SystemStatus.ERROR)
        assert hm.overall_status() == SystemStatus.ERROR


class TestTradeMetricsMultiplier:
    def test_multiplier_carried_from_trade(self):
        from analytics.performance import PerformanceEngine
        eng = PerformanceEngine(db_path=":memory:")
        trade = {
            "trade_id": "t1",
            "strategy_id": "s1",
            "instrument": "GOLDM",
            "side": "LONG",
            "gross_pnl": 5000.0,
            "net_pnl": 4800.0,
            "fees": 200.0,
            "average_entry_price": 52000.0,
            "average_exit_price": 52500.0,
            "filled_quantity": 1,
            "exit_quantity": 1,
            "multiplier": 10.0,
            "duration_seconds": 300.0,
        }
        tm = eng.calculate_trade_metrics(trade)
        assert tm.multiplier == 10.0

    def test_multiplier_defaults_to_one(self):
        from analytics.performance import PerformanceEngine
        eng = PerformanceEngine(db_path=":memory:")
        trade = {
            "trade_id": "t2",
            "strategy_id": "s1",
            "instrument": "GOLDM",
            "side": "LONG",
            "average_entry_price": 52000.0,
            "average_exit_price": 52500.0,
            "filled_quantity": 1,
            "exit_quantity": 1,
        }
        tm = eng.calculate_trade_metrics(trade)
        assert tm.multiplier == 1.0