"""Regression test for the `-1` no-data sentinel corruption (the live incident).

Confirms a non-positive / non-finite price is NEVER able to:
  1) trigger a stop-loss exit in the DEMA strategy (tick path),
  2) produce a broker fill at an invalid price,
  3) book a closed trade through TradeCloseManager,
  4) survive a paper-broker restore as a usable live price.

The live incident booked two silver fake closes at exit price -1.0 by chaining
all four of these; each is guarded independently now.
"""
from __future__ import annotations

import math

import pytest

from execution.paper_broker import PaperExecutionEngine, OrderState
from core.trade_close import TradeCloseManager
from strategies.base_dema_strategy import BaseDEMAStrategy
from strategies.types import Signal, SignalType


# --------------------------------------------------------------------------- #
# 1) Stop-loss must NOT fire on an invalid LTP
# --------------------------------------------------------------------------- #
class _FakeBase(BaseDEMAStrategy):
    """A strategy with minimal state; on_tick only touches the guarded fields."""

    def __init__(self, position_side, stop_price):
        self.strategy_id = "t0"
        self.instrument = "SILVERM"
        self.quantity = 1
        self.enabled = True
        self.just_entered = False
        self.pending_exit_at_open = False
        self.position_side = position_side
        self.stop_price = stop_price
        self.pending_entry = None
        self._closed = False

    def _create_exit_signal(self, reason, price, ts):
        self._closed = True
        return Signal(
            SignalType.SHORT, self.instrument, self.strategy_id, ts,
            trigger_price=price, stop_price=self.stop_price, quantity=self.quantity,
            side="SHORT", metadata={},
        )

    def _close_position(self, reason, price, ts):
        self._closed = True


def test_stop_loss_ignores_negative_sentinel():
    s = _FakeBase(position_side="LONG", stop_price=240900.0)
    sig = s.on_tick(-1.0, 1000.0)  # the incident sentinel
    assert sig is None, "a -1 tick must NOT trigger/exits"
    assert not s._closed, "stop-loss must not close on -1"


def test_stop_loss_ignores_nan_and_zero():
    for bad in (math.nan, 0.0, -0.0, float("inf")):
        s = _FakeBase(position_side="LONG", stop_price=240900.0)
        sig = s.on_tick(bad, 1000.0)
        assert sig is None, f"invalid ltp {bad} must be ignored"
        assert not s._closed


def test_stop_loss_still_fires_on_real_ltp():
    s = _FakeBase(position_side="LONG", stop_price=240900.0)
    sig = s.on_tick(240800.0, 1000.0)  # genuinely below stop
    assert sig is not None, "a real stop-out must still work"
    assert s._closed


# --------------------------------------------------------------------------- #
# 2) Broker must not fill at an invalid price
# --------------------------------------------------------------------------- #
def test_broker_rejects_negative_price():
    broker = PaperExecutionEngine(slippage_ticks=1)
    broker.update_price("SILVERM", -1.0)  # poison
    order = broker.create_order(
        Signal(SignalType.SHORT, "SILVERM", "s0", 1.0,
               trigger_price=240000.0, stop_price=240900.0, quantity=1),
        multiplier=5.0,
        trade_id="TRD-SENTINEL-1",
    )
    broker.submit_order(order)
    assert order.state == OrderState.REJECTED, "must reject, not fill at -1"
    assert broker.get_fills(strategy_id="s0") == []


def test_broker_rejects_zero_and_nan_price():
    for bad in (0.0, math.nan):
        broker = PaperExecutionEngine(slippage_ticks=1)
        broker.update_price("SILVERM", bad)
        order = broker.create_order(
            Signal(SignalType.LONG, "SILVERM", "s1", 1.0,
                   trigger_price=240000.0, stop_price=239500.0, quantity=1),
            multiplier=5.0,
            trade_id="TRD-SENTINEL-2",
        )
        broker.submit_order(order)
        assert order.state == OrderState.REJECTED


def test_broker_restore_drops_negative_price():
    broker = PaperExecutionEngine(slippage_ticks=1)
    broker.restore({"current_prices": {"SILVERM": -1.0}, "fills": [], "orders": []})
    assert broker._current_prices.get("SILVERM") is None


# --------------------------------------------------------------------------- #
# 3) TradeCloseManager must refuse a close at an invalid price
# --------------------------------------------------------------------------- #
def test_trade_close_refuses_negative_exit_price():
    pm = _StubPositionManager()
    tcm = TradeCloseManager(
        position_manager=pm, pnl_engines={}, account_engines={},
        global_account=None, risk_engine=None, persistence=None,
        event_store=None, telegram=None, event_callback=None, trade_ledger=None,
    )
    fill = _FakeFill(price=-1.0)
    closed = tcm.close_position(fill, _FakePosition(), "s0", 5.0, "stop_loss_hit")
    assert closed is False, "must refuse a close at -1"


class _StubPositionManager:
    def close_position(self, *a, **k):
        return True


class _FakePosition:
    position_id = "p0"
    strategy_id = "s0"
    instrument = "SILVERM"
    is_open = True
    is_long = True
    quantity = 1
    average_entry = 240000.0
    entry_timestamp = 1.0
    multiplier = 5.0
    margin = 100000.0
    entry_fill_ids = ["f0"]
    exit_reason = None


class _FakeFill:
    def __init__(self, price):
        self.fill_id = "fx0"
        self.order_id = ""
        self.strategy_id = "s0"
        self.instrument = "SILVERM"
        self.side = "SELL"
        self.quantity = 1
        self.price = price
        self.multiplier = 5.0
        self.timestamp = 2000.0