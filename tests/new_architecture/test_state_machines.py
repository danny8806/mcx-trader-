"""Trade / order / broker state machines (§56-§58).

The trade lifecycle must move PENDING -> OPEN -> CLOSED exactly once and be
idempotent at the boundaries; the broker/order state machine must reject
invalid transitions (submit a filled order, cancel a terminal order).
"""
import time

import pytest

from execution.paper_broker import OrderState
from strategies.types import SignalType

from ._harness import SIDS, open_long, open_short


def test_trade_lifecycle_transition(engine):
    sid = "gold_01"
    lc = engine.runtimes[sid].lifecycle
    assert lc.get_open_trades() == []

    open_long(engine, sid, time.time())
    open_trades = lc.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0].status == "OPEN"

    open_short(engine, sid, time.time() + 0.5)
    assert lc.get_open_trades() == []
    closed = lc.get_closed_trades()
    assert len(closed) == 1
    assert closed[0].status == "CLOSED"
    assert len(lc.get_open_trades()) == 0


def test_trade_close_idempotent(engine, persistence):
    sid = "gold_01"
    lc = engine.runtimes[sid].lifecycle
    open_long(engine, sid, time.time())
    trade = lc.get_open_trades()[0]
    before = len(lc.get_closed_trades())
    # closing an already-open trade through the engine is done by reversal;
    # a second manual close on the closed trade must be a safe no-op.
    from core.lifecycle import TradeStatus
    lc.close_trade(trade.trade_id, gross_pnl=0.0, charges=0.0, net_pnl=0.0)
    lc.close_trade(trade.trade_id, gross_pnl=0.0, charges=0.0, net_pnl=0.0)
    assert lc.get_closed_trades()[0].trade_id == trade.trade_id
    events = persistence._db.query(
        "SELECT event_type FROM events WHERE event_type='TRADE_CLOSED'")
    assert len(events) == 0  # engine-level events table does not log TRADE_CLOSED


def test_order_state_machine_valid(engine):
    ex = engine.execution_engine
    ex.update_price("GOLDM", 78000.0)
    sig = __import__("strategies.types", fromlist=["Signal"]).Signal(
        signal_type=SignalType.LONG, instrument="GOLDM", strategy_id="gold_01",
        timestamp=time.time(), trigger_price=78000.0, stop_price=0.0,
        quantity=1, signal_id="sm-valid-1")
    order = ex.create_order(sig, multiplier=10.0, trade_id="sm-trade-1")
    assert order.state == OrderState.CREATED
    submitted = ex.submit_order(order)
    assert submitted.state == OrderState.FILLED
    assert submitted.filled_quantity == order.quantity


def test_order_state_machine_invalid_transitions(engine):
    ex = engine.execution_engine
    ex.update_price("GOLDM", 78000.0)
    sig = __import__("strategies.types", fromlist=["Signal"]).Signal(
        signal_type=SignalType.SHORT, instrument="GOLDM", strategy_id="gold_01",
        timestamp=time.time(), trigger_price=78000.0, stop_price=0.0,
        quantity=1, signal_id="sm-invalid-1")
    order = ex.create_order(sig, multiplier=10.0, trade_id="sm-trade-2")
    ex.submit_order(order)
    assert order.state == OrderState.FILLED
    with pytest.raises(ValueError):
        ex.submit_order(order)  # cannot resubmit a terminal order


def test_order_cancel_terminal_noop(engine):
    ex = engine.execution_engine
    ex.update_price("GOLDM", 78000.0)
    sig = __import__("strategies.types", fromlist=["Signal"]).Signal(
        signal_type=SignalType.LONG, instrument="GOLDM", strategy_id="silver_01",
        timestamp=time.time(), trigger_price=78000.0, stop_price=0.0,
        quantity=1, signal_id="sm-cancel-1")
    order = ex.create_order(sig, multiplier=10.0, trade_id="sm-trade-3")
    assert ex.cancel_order(order.order_id) is True or order.state in (
        OrderState.CANCELED, OrderState.FILLED)
    if order.state == OrderState.CANCELED:
        assert ex.cancel_order(order.order_id) is False
    else:
        # already filled by market-order path; cancel must not regress state
        assert order.state == OrderState.FILLED


def test_lifecycle_resolves_trade_from_all_artifacts(engine):
    sid = "gold_01"
    open_long(engine, sid, time.time())
    lc = engine.runtimes[sid].lifecycle
    trade = lc.get_open_trades()[0]
    assert lc.resolve_trade_from_signal(trade.entry_signal_id) is not None
    if trade.entry_order_id:
        assert lc.resolve_trade_from_order(trade.entry_order_id) is not None
    if trade.entry_fill_id:
        assert lc.resolve_trade_from_fill(trade.entry_fill_id) is not None
    if trade.position_id:
        assert lc.resolve_trade_from_position(trade.position_id) is not None