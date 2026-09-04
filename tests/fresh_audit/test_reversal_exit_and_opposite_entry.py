"""REVERSAL FLOW DEEP TEST — exit current trade + place opposite trade order.

Proves the complete reversal flow for all 4 strategies:

  1) Current trade exits (via deferred exit at next bar open)
  2) Opposite trade order IS placed (via pending breakout on later bar)
  3) Old trade is CLOSED and purged from open cache
  4) New opposite trade is OPEN in the ledger

Also verifies SL does NOT place any opposite order (asymmetry with reversal).
"""
import json
import time
from pathlib import Path

import pytest

from analytics.schema import init_analytics_db
from analytics.trade_ledger import TradeLedger
from strategies.types import Signal, SignalType
from trading_engine import Bar

ALL4 = ["gold_01", "gold_02", "silver_01", "silver_02"]
INST = {"gold_01": "GOLDM", "gold_02": "GOLDM",
        "silver_01": "SILVERM", "silver_02": "SILVERM"}


def _write_config(root: Path) -> Path:
    data = {
        "system": {"name": "RevTest", "version": "1.0.0", "environment": "paper",
                   "log_level": "INFO",
                   "db_path": str(root / "data" / "db" / "trading.db"),
                   "state_path": str(root / "data" / "db" / "system_state.json")},
        "dhan": {"client_id": "TEST", "access_token": "", "ws_url": "wss://fake",
                 "rest_base": "https://fake", "token_file": str(root / "data" / "db" / "dhan_token.json"),
                 "pin": "", "totp_secret": ""},
        "instruments": {
            "GOLDM": {"symbol": "MCX:GOLDM202610", "security_id": "569003",
                      "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                      "multiplier": 10.0, "tick_size": 1.0, "lot_size": 1,
                      "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                      "margin_model": {"slope": 0.125, "intercept": 126930.0}},
            "SILVERM": {"symbol": "MCX:SILVERM202611", "security_id": "483080",
                        "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                        "multiplier": 5.0, "tick_size": 1.0, "lot_size": 1,
                        "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                        "margin_model": {"slope": 0.0625, "intercept": 142900.0}},
        },
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": {
            "gold_01": {"instrument": "GOLDM", "fast_timeframe": "5m", "mid_timeframe": "15m",
                        "htf_timeframe": "1h", "quantity": 1, "capital": 500000, "enabled": True},
            "gold_02": {"instrument": "GOLDM", "fast_timeframe": "15m", "mid_timeframe": "1h",
                        "htf_timeframe": "1h", "quantity": 1, "capital": 500000, "enabled": True},
            "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m", "mid_timeframe": "15m",
                          "htf_timeframe": "1h", "quantity": 1, "capital": 500000, "enabled": True},
            "silver_02": {"instrument": "SILVERM", "fast_timeframe": "5m", "mid_timeframe": "15m",
                          "htf_timeframe": "1h", "quantity": 1, "capital": 500000, "enabled": True},
        },
        "paper_execution": {"slippage_ticks": 0, "latency_ms": 0, "partial_fill_probability": 0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01, "exchange_pct": 0.0026,
                      "sebi_pct": 0.0001, "gst_pct": 18.0, "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01, "exchange_pct": 0.0026,
                        "sebi_pct": 0.0001, "gst_pct": 18.0, "stamp_duty_pct": 0.0},
        },
        "risk": {"max_open_positions_per_strategy": 1, "max_open_positions_total": 8,
                 "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": False},
        "account": {"starting_capital": 2000000.0,
                    "starting_capital_per_strategy": 500000.0, "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    cfg = root / "settings.json"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return cfg


@pytest.fixture()
def _engine(tmp_path, monkeypatch):
    from tests.fresh_audit import test_full_deep_architecture as harness
    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)
    cfg_path = _write_config(tmp_path)
    init_analytics_db(str(tmp_path / "data" / "db" / "analytics.db"))

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


def _price(instrument: str) -> float:
    return 78000.0 if instrument == "GOLDM" else 239000.0


def _process(engine, strategy_id, signal_type, price, ts, exit_meta=None):
    inst = INST[strategy_id]
    sig = Signal(signal_type=signal_type, instrument=inst, strategy_id=strategy_id,
                 timestamp=ts, trigger_price=price, stop_price=0.0, quantity=1,
                 metadata=exit_meta)
    engine.execution_engine.update_price(inst, price)
    engine._process_signal(sig)


def _open_long(engine, strategy_id, ts):
    _process(engine, strategy_id, SignalType.LONG, _price(INST[strategy_id]), ts)


def _open_short(engine, strategy_id, ts):
    _process(engine, strategy_id, SignalType.SHORT, _price(INST[strategy_id]), ts)


def _rejections(engine, sid):
    return engine.event_store.get_events_for_strategy(sid, "ORDER_REJECTED", 100)


def _make_bar(instrument, ts, open_, high, low, close, tf="5m"):
    return Bar(instrument=instrument, timeframe=tf, start_ts=ts,
               end_ts=ts + 300.0, open=open_, high=high, low=low, close=close)


# ============================================================================
# TEST 1: Reversal exits current trade AND places opposite trade order
# ============================================================================
@pytest.mark.parametrize("sid", ALL4)
def test_reversal_exits_current_trade_and_places_opposite(_engine, sid):
    """Complete reversal flow: LONG → SHORT reversal.

    Step 1: Open LONG
    Step 2: Arm reversal (strategy arms pending_exit_at_open + pending_entry SHORT)
    Step 3: Fire deferred exit (closes LONG at bar.open)
    Step 4: Feed bar that crosses SHORT trigger (fills SHORT entry)
    Step 5: Verify LONG is CLOSED+purged, SHORT is OPEN with correct entry
    """
    inst = INST[sid]
    tl = _engine.trade_ledger
    strat = _engine.strategies[sid]

    # Step 1: Open LONG
    _open_long(_engine, sid, ts=100.0)
    assert tl.count_trades(strategy_id=sid) == 1
    assert tl.get_open_trades(strategy_id=sid) != []

    # Step 2: Arm reversal — strategy creates pending_entry SHORT + pending_exit_at_open
    strat.position_side = "LONG"
    strat.stop_price = _price(inst) - 500.0
    strat.state = "LONG_POSITION"
    # Simulate what _create_reversal_signal does
    from strategies.types import PendingEntry
    trigger = _price(inst) - 100.0  # SHORT trigger = bar's low
    strat.pending_entry = PendingEntry(
        signal=Signal(signal_type=SignalType.SHORT, instrument=inst,
                      strategy_id=sid, timestamp=200.0,
                      trigger_price=trigger, stop_price=_price(inst) + 500.0,
                      quantity=1, side="SHORT"),
        trigger_price=trigger,
        side="SHORT",
        created_at=time.time(),
    )
    strat.pending_exit_at_open = True
    strat.pending_exit_reason = "short_reversal"

    # Step 3: Fire deferred exit at bar.open — closes LONG
    bar_exit = _make_bar(inst, ts=200.0, open_=_price(inst) - 80.0,
                         high=_price(inst) - 50.0, low=_price(inst) - 100.0,
                         close=_price(inst) - 80.0)
    _engine.execution_engine.update_price(inst, _price(inst) - 80.0)
    done = _engine._process_deferred_exit(strat, bar_exit)
    assert done is True, "deferred exit must be consumed"

    # LONG must be CLOSED and purged from open cache
    assert tl.get_open_trades(strategy_id=sid) == [], \
        f"{sid}: LONG must be purged from open cache after reversal exit"
    closed = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert len(closed) == 1, f"{sid}: exactly 1 CLOSED trade after reversal exit"
    assert closed[0].side == "LONG"

    # Step 4: Feed bar that crosses SHORT trigger (trigger = price - 100)
    # Bar low = trigger - 10 → crosses the SHORT trigger
    bar_trigger = _make_bar(inst, ts=250.0,
                            open_=trigger + 5, high=trigger + 5,
                            low=trigger - 10, close=trigger - 5)
    signal = strat._check_pending_entry(bar_trigger)
    assert signal is not None, f"{sid}: pending SHORT entry must be triggered by breakout bar"

    # Step 5: Process the entry signal through the engine
    _engine.execution_engine.update_price(inst, trigger)
    _engine._process_signal(signal)

    # SHORT must now be OPEN
    open_pos = [p for p in _engine.position_manager.get_positions_by_strategy(sid) if p.is_open]
    assert len(open_pos) == 1, f"{sid}: SHORT must be open after breakout"
    assert open_pos[0].is_short

    # SHORT trade must be OPEN in the ledger
    open_trades = tl.get_open_trades(strategy_id=sid)
    assert len(open_trades) == 1, f"{sid}: exactly 1 OPEN trade (SHORT) after reversal"
    assert open_trades[0].side == "SHORT"
    assert open_trades[0].status == "OPEN"

    # Total trades: 1 CLOSED (LONG) + 1 OPEN (SHORT) = 2
    assert tl.count_trades(strategy_id=sid) == 2
    assert len(_rejections(_engine, sid)) == 0, f"{sid}: no rejections"


@pytest.mark.parametrize("sid", ALL4)
def test_reversal_short_to_long_exits_and_places_opposite(_engine, sid):
    """Complete reversal flow: SHORT → LONG reversal."""
    inst = INST[sid]
    tl = _engine.trade_ledger
    strat = _engine.strategies[sid]

    # Step 1: Open SHORT
    _open_short(_engine, sid, ts=100.0)
    assert tl.count_trades(strategy_id=sid) == 1

    # Step 2: Arm reversal — LONG pending entry + deferred exit
    strat.position_side = "SHORT"
    strat.stop_price = _price(inst) + 500.0
    strat.state = "SHORT_POSITION"
    from strategies.types import PendingEntry
    trigger = _price(inst) + 100.0  # LONG trigger = bar's high
    strat.pending_entry = PendingEntry(
        signal=Signal(signal_type=SignalType.LONG, instrument=inst,
                      strategy_id=sid, timestamp=200.0,
                      trigger_price=trigger, stop_price=_price(inst) - 500.0,
                      quantity=1, side="LONG"),
        trigger_price=trigger,
        side="LONG",
        created_at=time.time(),
    )
    strat.pending_exit_at_open = True
    strat.pending_exit_reason = "long_reversal"

    # Step 3: Fire deferred exit — closes SHORT
    bar_exit = _make_bar(inst, ts=200.0, open_=_price(inst) + 80.0,
                         high=_price(inst) + 100.0, low=_price(inst) + 50.0,
                         close=_price(inst) + 80.0)
    _engine.execution_engine.update_price(inst, _price(inst) + 80.0)
    done = _engine._process_deferred_exit(strat, bar_exit)
    assert done is True

    assert tl.get_open_trades(strategy_id=sid) == [], \
        f"{sid}: SHORT must be purged after reversal exit"
    closed = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert len(closed) == 1
    assert closed[0].side == "SHORT"

    # Step 4: Bar crosses LONG trigger
    bar_trigger = _make_bar(inst, ts=250.0,
                            open_=trigger - 5, high=trigger + 10,
                            low=trigger - 5, close=trigger + 5)
    signal = strat._check_pending_entry(bar_trigger)
    assert signal is not None, f"{sid}: pending LONG entry must trigger"

    _engine.execution_engine.update_price(inst, trigger)
    _engine._process_signal(signal)

    open_pos = [p for p in _engine.position_manager.get_positions_by_strategy(sid) if p.is_open]
    assert len(open_pos) == 1
    assert open_pos[0].is_long

    open_trades = tl.get_open_trades(strategy_id=sid)
    assert len(open_trades) == 1
    assert open_trades[0].side == "LONG"
    assert tl.count_trades(strategy_id=sid) == 2
    assert len(_rejections(_engine, sid)) == 0


# ============================================================================
# TEST 2: SL exit does NOT place any opposite order (asymmetry)
# ============================================================================
@pytest.mark.parametrize("sid", ALL4)
def test_sl_exit_does_not_arm_any_entry(_engine, sid):
    """SL exit must NOT create a pending_entry or any opposite order.

    After SL: strategy goes FLAT, no pending_entry, open cache empty.
    """
    inst = INST[sid]
    tl = _engine.trade_ledger
    strat = _engine.strategies[sid]

    _open_long(_engine, sid, ts=100.0)
    assert tl.count_trades(strategy_id=sid) == 1

    # Fire SL exit
    sl_exit = Signal(
        signal_type=SignalType.SHORT, instrument=inst, strategy_id=sid,
        timestamp=300.0, trigger_price=_price(inst) - 5.0, stop_price=0.0,
        quantity=1, metadata={"exit": True, "exit_reason": "stop_loss_hit"},
    )
    _engine.execution_engine.update_price(inst, sl_exit.trigger_price)
    _engine._process_signal(sl_exit)

    # No pending_entry after SL
    assert strat.pending_entry is None, f"{sid}: SL must NOT arm pending_entry"
    assert strat.pending_exit_at_open is False, f"{sid}: SL must NOT set pending_exit_at_open"
    assert strat.position_side is None, f"{sid}: position must be FLAT after SL"

    # Only 1 trade (CLOSED), no opposite
    assert tl.count_trades(strategy_id=sid) == 1
    assert tl.get_open_trades(strategy_id=sid) == []
    assert len(_rejections(_engine, sid)) == 0


# ============================================================================
# TEST 3: Reversal + SL sequence (full lifecycle)
# ============================================================================
@pytest.mark.parametrize("sid", ALL4)
def test_reversal_then_sl_full_lifecycle(_engine, sid):
    """Complete lifecycle: LONG → reversal close → SHORT → SL close.

    Verifies:
    - LONG CLOSED + purged after reversal
    - SHORT OPEN after breakout
    - SHORT CLOSED + purged after SL
    - No phantom trades at any point
    """
    inst = INST[sid]
    tl = _engine.trade_ledger
    strat = _engine.strategies[sid]

    # 1. Open LONG
    _open_long(_engine, sid, ts=100.0)
    assert tl.count_trades(strategy_id=sid) == 1

    # 2. Arm reversal SHORT
    strat.position_side = "LONG"
    strat.stop_price = _price(inst) - 500.0
    strat.state = "LONG_POSITION"
    from strategies.types import PendingEntry
    trigger = _price(inst) - 100.0
    strat.pending_entry = PendingEntry(
        signal=Signal(signal_type=SignalType.SHORT, instrument=inst,
                      strategy_id=sid, timestamp=200.0,
                      trigger_price=trigger, stop_price=_price(inst) + 500.0,
                      quantity=1, side="SHORT"),
        trigger_price=trigger, side="SHORT", created_at=time.time(),
    )
    strat.pending_exit_at_open = True
    strat.pending_exit_reason = "short_reversal"

    # 3. Deferred exit closes LONG
    bar_exit = _make_bar(inst, ts=200.0, open_=_price(inst) - 80.0,
                         high=_price(inst) - 50.0, low=_price(inst) - 100.0,
                         close=_price(inst) - 80.0)
    _engine.execution_engine.update_price(inst, _price(inst) - 80.0)
    _engine._process_deferred_exit(strat, bar_exit)
    assert tl.get_open_trades(strategy_id=sid) == []

    # 4. Breakout triggers SHORT entry
    bar_trigger = _make_bar(inst, ts=250.0,
                            open_=trigger + 5, high=trigger + 5,
                            low=trigger - 10, close=trigger - 5)
    signal = strat._check_pending_entry(bar_trigger)
    assert signal is not None
    _engine.execution_engine.update_price(inst, trigger)
    _engine._process_signal(signal)
    assert tl.count_trades(strategy_id=sid) == 2

    # 5. SL closes SHORT
    sl_exit = Signal(
        signal_type=SignalType.LONG, instrument=inst, strategy_id=sid,
        timestamp=300.0, trigger_price=trigger + 50.0, stop_price=0.0,
        quantity=1, metadata={"exit": True, "exit_reason": "stop_loss_hit"},
    )
    _engine.execution_engine.update_price(inst, trigger + 50.0)
    _engine._process_signal(sl_exit)

    # Final state: 2 CLOSED trades, 0 OPEN
    assert tl.count_trades(strategy_id=sid) == 2
    assert tl.get_open_trades(strategy_id=sid) == []
    closed = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert len(closed) == 2
    assert {c.side for c in closed} == {"LONG", "SHORT"}
    assert len(_rejections(_engine, sid)) == 0


# ============================================================================
# TEST 4: Live gold pattern — LONG reversal + SHORT SL
# ============================================================================
def test_live_gold_long_reversal_then_short_sl(_engine):
    """Reproduce gold_01 live pattern with FULL reversal flow."""
    sid = "gold_01"
    inst = "GOLDM"
    tl = _engine.trade_ledger
    strat = _engine.strategies[sid]
    from strategies.types import PendingEntry

    # 1. Open LONG at 150768
    _process(_engine, sid, SignalType.LONG, 150768.0, ts=100.0)
    assert tl.count_trades(strategy_id=sid) == 1

    # 2. Arm SHORT reversal — trigger = low of the signal bar where crossover detected.
    # SHORT breakout fires when a later bar's low < trigger_price.
    strat.position_side = "LONG"
    strat.stop_price = 150200.0
    strat.state = "LONG_POSITION"
    trigger = 152900.0  # low of signal bar (SHORT trigger)
    strat.pending_entry = PendingEntry(
        signal=Signal(signal_type=SignalType.SHORT, instrument=inst,
                      strategy_id=sid, timestamp=200.0,
                      trigger_price=trigger, stop_price=153400.0,
                      quantity=1, side="SHORT"),
        trigger_price=trigger, side="SHORT", created_at=time.time(),
    )
    strat.pending_exit_at_open = True
    strat.pending_exit_reason = "short_reversal"

    # 3. Deferred exit closes LONG at 150717 (next bar open)
    bar_exit = _make_bar(inst, ts=200.0, open_=150717.0,
                         high=150750.0, low=150680.0, close=150717.0)
    _engine.execution_engine.update_price(inst, 150717.0)
    _engine._process_deferred_exit(strat, bar_exit)
    assert tl.get_open_trades(strategy_id=sid) == []
    closed_long = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert len(closed_long) == 1
    assert closed_long[0].side == "LONG"

    # 4. Breakout bar: low < trigger (152900) → SHORT fills at trigger level
    bar_trigger = _make_bar(inst, ts=250.0,
                            open_=152910.0, high=152920.0,
                            low=152850.0, close=152860.0)
    signal = strat._check_pending_entry(bar_trigger)
    assert signal is not None, "SHORT breakout must trigger (bar.low < trigger)"
    _engine.execution_engine.update_price(inst, trigger)
    _engine._process_signal(signal)
    assert tl.count_trades(strategy_id=sid) == 2

    # 5. SL closes SHORT at 153501
    sl_exit = Signal(signal_type=SignalType.LONG, instrument=inst, strategy_id=sid,
                     timestamp=300.0, trigger_price=153501.0, stop_price=0.0,
                     quantity=1, metadata={"exit": True, "exit_reason": "stop_loss_hit"})
    _engine.execution_engine.update_price(inst, 153501.0)
    _engine._process_signal(sl_exit)

    closed = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert len(closed) == 2
    assert {c.side for c in closed} == {"LONG", "SHORT"}
    assert tl.get_open_trades(strategy_id=sid) == []
    assert tl.count_trades(strategy_id=sid) == 2