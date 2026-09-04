"""Tests for the REST-driven trading gate.

The WebSocket is ONLY a live-LTP display feed. REST candles are the authoritative
source of truth for signals. When Dhan's MCX WebSocket tick feed is silent (which
it intermittently is — confirmed even via Dhan's official SDK), the engine must
still be able to enter TRADING / allow trading as long as REST candle data keeps
confirming real live prices. These tests pin that behaviour.
"""
from __future__ import annotations

import time

import pytest

from core.market_status import (
    MarketStatus,
    MarketState,
    EngineStatus,
    DataStatus,
)


class TestRestDataFreshness:
    def test_mark_rest_data_fresh_promotes_to_connected(self):
        ms = MarketStatus()
        assert ms.data_status == DataStatus.NO_DATA
        assert ms.has_live_market_data is False
        ms.mark_rest_data_fresh()
        assert ms.data_status == DataStatus.CONNECTED
        assert ms.has_live_market_data is True

    def test_mark_rest_data_fresh_promotes_disconnected_to_connected(self):
        ms = MarketStatus()
        ms.update_data_status(connected=False)
        assert ms.data_status == DataStatus.DISCONNECTED
        ms.mark_rest_data_fresh()
        assert ms.data_status == DataStatus.CONNECTED
        assert ms.has_live_market_data is True

    def test_has_live_market_data_true_when_ws_silent_but_rest_fresh(self):
        # Simulate the real failure mode: WS connected but delivered 0 ticks
        # (last_tick_time=0 => NO_DATA), while REST candles keep flowing.
        ms = MarketStatus()
        ms.update_data_status(connected=True, last_tick_time=0)
        assert ms.data_status == DataStatus.NO_DATA
        assert ms.has_live_market_data is False
        ms.mark_rest_data_fresh()
        assert ms.has_live_market_data is True

    def test_has_live_market_data_stale_after_rest_threshold(self):
        ms = MarketStatus()
        ms.mark_rest_data_fresh()
        assert ms.has_live_market_data is True
        # Age the REST heartbeat past the (generous) REST staleness threshold.
        ms._rest_last_tick_time = time.time() - ms._rest_stale_threshold - 1
        # data_status was promoted to CONNECTED; a fresh WS-less downgrade path:
        ms.update_data_status(connected=True, last_tick_time=0)
        assert ms.has_live_market_data is False

    def test_update_data_status_does_not_downgrade_while_rest_fresh(self):
        ms = MarketStatus()
        ms.mark_rest_data_fresh()
        # Even a WS connected-but-silent call must not clobber CONNECTED while
        # REST data is freshly confirmed.
        ms.update_data_status(connected=True, last_tick_time=0)
        assert ms.data_status == DataStatus.CONNECTED
        assert ms.has_live_market_data is True


class TestRestTradingGate:
    def test_is_trading_allowed_true_via_rest_alone(self):
        ms = MarketStatus()
        ms.force_state(MarketState.LIVE_TRADING)
        ms.set_engine_status(EngineStatus.TRADING)
        # No WebSocket tick ever arrived; only REST confirmation.
        ms.mark_rest_data_fresh()
        assert ms.is_trading_allowed is True

    def test_is_trading_allowed_false_without_live_data(self):
        ms = MarketStatus()
        ms.force_state(MarketState.LIVE_TRADING)
        ms.set_engine_status(EngineStatus.TRADING)
        assert ms.is_trading_allowed is False

    def test_is_trading_allowed_false_outside_live_trading(self):
        ms = MarketStatus()
        ms.force_state(MarketState.OVERNIGHT)
        ms.set_engine_status(EngineStatus.TRADING)
        ms.mark_rest_data_fresh()
        assert ms.is_trading_allowed is False

    def test_is_trading_allowed_false_before_engine_trading(self):
        ms = MarketStatus()
        ms.force_state(MarketState.LIVE_TRADING)
        ms.set_engine_status(EngineStatus.READY)
        ms.mark_rest_data_fresh()
        assert ms.is_trading_allowed is False


class _EngineStub:
    """Minimal engine surface needed by TradingEngine._maybe_enable_trading."""

    def __init__(self, market_status):
        self.market_status = market_status


def test_maybe_enable_trading_transitions_on_rest_fresh_without_ws():
    # Test the extracted transition helper directly (it is called from both the
    # WebSocket _on_tick path and the REST _on_bar_closed path).
    from trading_engine import TradingEngine

    ms = MarketStatus()
    ms.force_state(MarketState.LIVE_TRADING)
    ms.set_engine_status(EngineStatus.READY)
    stub = _EngineStub(ms)

    # Borrow the engine's helper on a bare instance without running the engine.
    helper = TradingEngine._maybe_enable_trading
    assert ms.engine_status == EngineStatus.READY

    # No live data yet -> no transition.
    helper(stub)
    assert ms.engine_status == EngineStatus.READY

    # REST confirms live data (WS silent) -> READY -> TRADING.
    ms.mark_rest_data_fresh()
    helper(stub)
    assert ms.engine_status == EngineStatus.TRADING
    assert ms.is_trading_allowed is True


class TestReconcileStrategyPositions:
    """Verify that _reconcile_strategy_positions heals a desync where
    a strategy is persisted as FLAT but the position manager has an
    open position for it."""

    def test_reconcile_heals_flat_strategy_with_open_position(self):
        from unittest.mock import MagicMock
        from strategies.types import StrategyState
        from trading_engine import TradingEngine

        engine = MagicMock()
        engine._lock = __import__("threading").RLock()

        strat = MagicMock()
        strat.strategy_id = "gold_01"
        strat.state = StrategyState.FLAT
        strat.position_side = None
        strat.stop_price = None

        pos = MagicMock()
        pos.is_open = True
        pos.side = MagicMock()
        pos.side.value = "LONG"
        pos.average_entry = 151244.0
        pos.stop_price = 150965.0

        engine.strategies = {"gold_01": strat}
        engine.position_manager.get_positions_by_strategy.return_value = [pos]

        TradingEngine._reconcile_strategy_positions(engine)

        assert strat.state == StrategyState.LONG_POSITION
        assert strat.position_side == "LONG"
        assert strat.stop_price == 150965.0

    def test_reconcile_does_not_touch_already_correct_strategy(self):
        from unittest.mock import MagicMock
        from strategies.types import StrategyState
        from trading_engine import TradingEngine

        engine = MagicMock()
        engine._lock = __import__("threading").RLock()

        strat = MagicMock()
        strat.strategy_id = "gold_01"
        strat.state = StrategyState.LONG_POSITION

        engine.strategies = {"gold_01": strat}
        engine.position_manager.get_positions_by_strategy.return_value = []

        TradingEngine._reconcile_strategy_positions(engine)
        assert strat.state == StrategyState.LONG_POSITION

    def test_reconcile_skips_flat_strategy_with_no_open_position(self):
        from unittest.mock import MagicMock
        from strategies.types import StrategyState
        from trading_engine import TradingEngine

        engine = MagicMock()
        engine._lock = __import__("threading").RLock()

        strat = MagicMock()
        strat.strategy_id = "silver_01"
        strat.state = StrategyState.FLAT
        strat.position_side = None
        strat.stop_price = None

        pos = MagicMock()
        pos.is_open = False

        engine.strategies = {"silver_01": strat}
        engine.position_manager.get_positions_by_strategy.return_value = [pos]

        TradingEngine._reconcile_strategy_positions(engine)

        assert strat.state == StrategyState.FLAT
        assert strat.position_side is None
