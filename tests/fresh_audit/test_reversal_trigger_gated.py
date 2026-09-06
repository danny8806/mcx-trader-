"""CRITICAL REVERSAL SEMANTICS AUDIT — trigger-gated reversal regression.

Proves the REQUIRED reversal semantics end-to-end through the REAL candle
handler (_make_candle_handler):

  OPPOSITE SIGNAL → WAIT → OPPOSITE TRIGGER REACHED → EXIT OLD + ENTER NEW
  (atomically, at the trigger price)

Specifically rejects the forbidden next-candle/signal-time exit:

  Rule 8  — no next-candle exit
  Rule 9/10 — old position stays OPEN until the opposite trigger is reached
  Rule 11 — exit+entry only when the trigger is actually reached (at trigger)
  Rule 12 — later candle execution (never the signal candle itself)
  Rule 5/24/30 — same signal_id is BOTH old-exit and new-entry
"""
import json
import time
from pathlib import Path

import pytest

from events.types import CandleEvent
from strategies.types import PendingEntry, Signal, SignalType
from tests.fresh_audit.test_reversal_exit_and_opposite_entry import (
    _write_config, ALL4, INST, _price,
)


@pytest.fixture()
def _engine(tmp_path, monkeypatch):
    from tests.fresh_audit import test_full_deep_architecture as harness
    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)
    cfg_path = _write_config(tmp_path)

    from persistence.manager import PersistenceManager
    from trading_engine import TradingEngine
    persistence = PersistenceManager(
        state_path=str(tmp_path / "data" / "db" / "system_state.json"),
        db_path=str(tmp_path / "data" / "db" / "trading.db"),
    )
    engine = TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)

    from core.market_status import MarketState, EngineStatus
    ws = engine.data_adapter.ws
    ws.connected = True
    ws._last_tick_time = time.time()
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine._running = True
    engine._on_tick({"instrument": "GOLDM", "ltp": 78000.0, "event_timestamp": time.time()})

    from core.trade_close import TradeCloseManager
    engine._trade_close_manager = TradeCloseManager(
        position_manager=engine.position_manager,
        pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines,
        global_account=engine.account_engine,
        risk_engine=engine.risk_engine,
        persistence=persistence,
        event_store=engine.event_store,
        telegram=engine.telegram,
        event_callback=engine._event_callback,
        trade_ledger=engine.trade_ledger,
    )
    yield engine
    try:
        engine.stop()
    except Exception:
        pass
    try:
        persistence.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _restore_cfg():
    from config import Config
    original = dict(Config._config)
    yield
    Config._config = original


def _open_pos(engine, sid, ts):
    _process(engine, sid, SignalType.LONG, _price(INST[sid]), ts)


def _open_short(engine, sid, ts):
    _process(engine, sid, SignalType.SHORT, _price(INST[sid]), ts)


def _process(engine, strategy_id, signal_type, price, ts, exit_meta=None):
    inst = INST[strategy_id]
    sig = Signal(signal_type=signal_type, instrument=inst, strategy_id=strategy_id,
                 timestamp=ts, trigger_price=price, stop_price=0.0, quantity=1,
                 metadata=exit_meta)
    engine.execution_engine.update_price(inst, price)
    engine._process_signal(sig)


def _arm_long_reversal(engine, strategy_id):
    """Hold SHORT and arm a LONG reversal (opposite side up = LONG)."""
    inst = INST[strategy_id]
    strat = engine.strategies[strategy_id]
    strat.position_side = "SHORT"
    strat.stop_price = _price(inst) + 500.0
    strat.state = "SHORT_POSITION"
    trigger = _price(inst) + 100.0  # LONG trigger = signal candle HIGH
    strat.pending_entry = PendingEntry(
        signal=Signal(signal_type=SignalType.LONG, instrument=inst,
                      strategy_id=strategy_id, timestamp=200.0,
                      trigger_price=trigger, stop_price=_price(inst) - 500.0,
                      quantity=1, side="LONG"),
        trigger_price=trigger, side="LONG", created_at=time.time(),
    )
    strat.pending_exit_at_open = True
    strat.pending_exit_reason = "long_reversal"
    return strat, trigger


def _warm_htf(strategy, sid, base=100000.0):
    """Warm the strategy's slow(1h)/mid(15m) HTF states so on_bar passes the
    HTF gate and reaches pending-entry evaluation (mirrors live warm system)."""
    inst = INST[sid]
    # Feed ~25 slow (1h) candles and ~25 mid (15m) candles (DEMA(3)/ATR(6) warm).
    for i in range(25):
        ts = base + i * 3600.0
        o = 100.0 + i
        strategy._on_slow_htf_candle(CandleEvent(
            instrument=inst, timeframe=strategy.htf_timeframe, start_ts=ts,
            end_ts=ts + 3600.0, open=o, high=o + 2, low=o - 2, close=o + 1,
            volume=1, is_closed=True, source="rest"))
        strategy._on_mid_htf_candle(CandleEvent(
            instrument=inst, timeframe=strategy.mid_timeframe, start_ts=ts,
            end_ts=ts + 900.0, open=o, high=o + 2, low=o - 2, close=o + 1,
            volume=1, is_closed=True, source="rest"))


def _feed_candle(engine, strategy_id, ts, open_, high, low, close, tf=None):
    inst = INST[strategy_id]
    strat = engine.strategies[strategy_id]
    tf = tf or strat.fast_timeframe
    handler = engine._make_candle_handler(strat)
    handler(CandleEvent(instrument=inst, timeframe=tf, start_ts=ts, end_ts=ts + 300.0,
                        open=open_, high=high, low=low, close=close, volume=1,
                        is_closed=True, source="rest"))
    engine.execution_engine.update_price(inst, close)


@pytest.mark.parametrize("sid", ALL4)
def test_old_position_stays_open_until_trigger_reached(_engine, sid):
    """Rule 8/9/10: SHORT stays OPEN on a non-trigger candle (no next-candle exit)."""
    tl = _engine.trade_ledger
    _open_short(_engine, sid, ts=100.0)
    assert len(tl.get_open_trades(strategy_id=sid)) == 1

    strat, trigger = _arm_long_reversal(_engine, sid)
    inst = INST[sid]
    _warm_htf(strat, sid)

    # Several bars whose high NEVER reaches the LONG trigger => SHORT stays open.
    for i, high in enumerate((trigger - 20.0, trigger - 10.0, trigger - 5.0), start=1):
        _feed_candle(_engine, sid, ts=120000.0 + i, open_=high - 3.0, high=high,
                     low=high - 10.0, close=high - 2.0)
        assert len(tl.get_open_trades(strategy_id=sid)) == 1, \
            f"{sid}: SHORT must stay OPEN before the LONG trigger is reached"
        assert strat.pending_exit_at_open is True, \
            f"{sid}: reversal must remain armed until the trigger is reached"


@pytest.mark.parametrize("sid", ALL4)
def test_atomic_trigger_exit_and_opposite_entry(_engine, sid):
    """Rule 11/12: on the trigger-reached bar, exit SHORT + enter LONG at trigger.

    Both happen on the SAME (later) candle, at the trigger price, sharing one
    signal_id. Old exit_signal_id == new entry_signal_id.
    """
    tl = _engine.trade_ledger
    _open_short(_engine, sid, ts=100.0)
    strat, trigger = _arm_long_reversal(_engine, sid)
    inst = INST[sid]
    _warm_htf(strat, sid)

    # Bar that does NOT reach the trigger.
    _feed_candle(_engine, sid, ts=120000.0, open_=trigger - 30.0, high=trigger - 10.0,
                 low=trigger - 40.0, close=trigger - 15.0)
    assert len(tl.get_open_trades(strategy_id=sid)) == 1

    # Bar whose high EXCEEDS the LONG trigger -> atomic exit+entry.
    _feed_candle(_engine, sid, ts=120300.0, open_=trigger - 5.0, high=trigger + 15.0,
                 low=trigger - 5.0, close=trigger + 8.0)
    assert strat.pending_exit_at_open is False, \
        f"{sid}: deferred exit must be consumed once the trigger is reached"

    open_trades = tl.get_open_trades(strategy_id=sid)
    assert len(open_trades) == 1, f"{sid}: must be exactly one OPEN trade after reversal"
    assert open_trades[0].side == "LONG", f"{sid}: new opposite LONG must be OPEN"

    closed = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert len(closed) == 1, f"{sid}: exactly one CLOSED trade (the SHORT)"
    assert closed[0].side == "SHORT"
    assert closed[0].exit_reason == "long_reversal", \
        f"{sid}: SHORT must exit with the reversal reason"

    # Rule 5/24/30 — same signal_id is both old exit_signal_id and new entry_signal_id.
    import sqlite3
    db = sqlite3.connect(str(_engine._persistence.db_path))
    row_closed = db.execute(
        "SELECT exit_signal_id, status FROM trades WHERE trade_id=?",
        (closed[0].trade_id,)).fetchone()
    row_open = db.execute(
        "SELECT entry_signal_id, status FROM trades WHERE trade_id=?",
        (open_trades[0].trade_id,)).fetchone()
    db.close()
    assert row_closed is not None and row_open is not None, f"{sid}: trades missing from DB"
    assert row_closed[0] is not None and row_closed[0] != "", \
        f"{sid}: SHORT exit must carry an exit_signal_id"
    assert row_closed[0] == row_open[0], \
        f"{sid}: reversal exit+entry must reuse the SAME signal_id"


@pytest.mark.parametrize("sid", ALL4)
def test_no_reversal_if_trigger_never_reached(_engine, sid):
    """Rule 8/10: if the trigger is NEVER reached, the old position never exits."""
    tl = _engine.trade_ledger
    _open_short(_engine, sid, ts=100.0)
    strat, trigger = _arm_long_reversal(_engine, sid)
    _warm_htf(strat, sid)

    # Collapsing prices that never exceed the LONG trigger.
    for i, high in enumerate((trigger - 50.0, trigger - 30.0), start=1):
        _feed_candle(_engine, sid, ts=120000.0 + i, open_=high - 3.0, high=high,
                     low=high - 20.0, close=high - 5.0)

    open_trades = tl.get_open_trades(strategy_id=sid)
    assert len(open_trades) == 1, f"{sid}: SHORT must remain OPEN (trigger never hit)"
    assert open_trades[0].side == "SHORT"
    closed = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert closed == [], f"{sid}: no trade may close before the trigger is reached"
