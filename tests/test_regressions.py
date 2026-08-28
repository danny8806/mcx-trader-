"""Portable regression coverage for trading-system safety invariants.

Run with the standard library: ``python -m unittest tests.test_regressions``.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.candle_fetcher import CandleFetcher
from core.risk_engine import RiskEngine
from execution.paper_broker import PaperExecutionEngine
from persistence.manager import PersistenceManager
from strategies.base_dema_strategy import BaseDEMAStrategy
from strategies.types import SignalType, StrategyState


IST = timezone(timedelta(hours=5, minutes=30))


class _Adapter:
    def __init__(self, candles):
        self.candles = candles

    def fetch_historical_candles(self, *args):
        return self.candles


class RegressionTests(unittest.TestCase):
    def test_persistence_first_write_does_not_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persistence = PersistenceManager(str(root / "state.json"), str(root / "trading.db"))
            started = time.monotonic()
            persistence.save_event({"event_type": "regression"})
            self.assertLess(time.monotonic() - started, 1.0)
            persistence.close()

    def test_fetcher_uses_completed_candle_start_timestamp(self):
        candle_start = datetime(2026, 8, 28, 9, 0, tzinfo=IST)
        received = []
        fetcher = CandleFetcher(
            _Adapter([[int(candle_start.timestamp()), 100, 103, 99, 102, 10]]),
            {"GOLDM": {}}, received.append,
        )
        fetcher._fetch_candle("GOLDM", {}, "5m", candle_start, "test")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].start_ts, candle_start.timestamp())

    def test_reversal_is_an_exit_then_pending_entry(self):
        strategy = BaseDEMAStrategy("s", "GOLDM", "5m", "1h")
        strategy.state = StrategyState.SHORT_POSITION
        strategy.position_side = "SHORT"
        signal = strategy._create_reversal_signal("LONG", 100, 101, 99, 1)
        self.assertEqual(signal.signal_type, SignalType.LONG)
        self.assertTrue(signal.metadata["exit"])
        self.assertEqual(strategy.state, StrategyState.PENDING_LONG)
        self.assertIsNotNone(strategy.pending_entry)

    def test_paper_execution_honors_configured_latency(self):
        engine = PaperExecutionEngine(latency_ms=15, partial_fill_probability=0)
        engine.update_price("GOLDM", 100)
        from strategies.types import Signal
        signal = Signal(SignalType.LONG, "GOLDM", "s", 1, 100, 90, 1)
        start = time.monotonic()
        order = engine.submit_order(engine.create_order(signal))
        self.assertGreaterEqual(time.monotonic() - start, 0.01)
        self.assertEqual(order.filled_quantity, 1)

    def test_risk_resets_before_order_check_on_new_ist_day(self):
        risk = RiskEngine(max_daily_loss=100)
        risk._daily_pnl = -100
        risk._last_reset_date = "2000-01-01"
        from strategies.types import Signal
        signal = Signal(SignalType.LONG, "GOLDM", "s", 1, 100, 90, 1)
        allowed, reason = risk.check_order(signal, 0, 0, 1_000, 1, 1_000)
        self.assertTrue(allowed, reason)


if __name__ == "__main__":
    unittest.main()
