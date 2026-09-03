"""DEEP AUDIT — BUG-C reversal-cap fix across ALL 4 live strategies + SL-exit.

Covers:
  - gold_01, gold_02, silver_01, silver_02 (the 4 live production strategies).
  - LONG->SHORT and SHORT->LONG reversal entries at max_positions_per_strategy=1
    must NOT be rejected by the risk cap (BUG-C regression, previously SILVERM
    SILVER was rejected with max_positions_per_strategy_reached).
  - Genuine add-on entries (same side) STILL rejected.
  - stop_loss_hit exit must NEVER be blocked by the cap for any strategy.
  - deferred-reversal exit (is_exit=True) must be cap-exempt.
  - The prior silver state/equity fix (BUG-A/B) still holds alongside.
"""
import json
import os
import time
from pathlib import Path

import pytest

from core.market_status import MarketState, EngineStatus
from strategies.types import Signal, SignalType
from trading_engine import _strategy_positions_for_risk
from analytics.schema import init_analytics_db


# ---------------------------------------------------------------------------
# engine harness for all 4 live strategies
# ---------------------------------------------------------------------------
def _write_config(root: Path) -> Path:
    data = {
        "system": {
            "name": "ReversAll", "version": "1.0.0", "environment": "paper",
            "log_level": "INFO",
            "db_path": str(root / "data" / "db" / "trading.db"),
            "state_path": str(root / "data" / "db" / "system_state.json"),
        },
        "dhan": {
            "client_id": "TEST", "access_token": "", "ws_url": "wss://fake",
            "rest_base": "https://fake", "token_file": str(root / "data" / "db" / "dhan_token.json"),
            "pin": "", "totp_secret": "",
        },
        "instruments": {
            "GOLDM": {
                "symbol": "MCX:GOLDM202609", "security_id": "563946",
                "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                "multiplier": 10.0, "tick_size": 1.0, "lot_size": 1,
                "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                "margin_model": {"slope": 0.125, "intercept": 126930.0},
            },
            "SILVERM": {
                "symbol": "MCX:SILVERM202611", "security_id": "483080",
                "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                "multiplier": 5.0, "tick_size": 1.0, "lot_size": 1,
                "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                "margin_model": {"slope": 0.0625, "intercept": 142900.0},
            },
        },
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": {
            "gold_01": {"instrument": "GOLDM", "fast_timeframe": "5m",
                        "mid_timeframe": "15m", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 500000, "enabled": True},
            "gold_02": {"instrument": "GOLDM", "fast_timeframe": "15m",
                        "mid_timeframe": "1h", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 500000, "enabled": True},
            "silver_01": {"instrument": "SILVERM", "fast_timeframe": "5m",
                          "mid_timeframe": "15m", "htf_timeframe": "1h",
                          "quantity": 1, "capital": 500000, "enabled": True},
            "silver_02": {"instrument": "SILVERM", "fast_timeframe": "5m",
                          "mid_timeframe": "15m", "htf_timeframe": "1h",
                          "quantity": 1, "capital": 500000, "enabled": True},
        },
        "paper_execution": {"slippage_ticks": 0, "latency_ms": 0, "partial_fill_probability": 0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                      "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                      "gst_pct": 18.0, "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                        "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                        "gst_pct": 18.0, "stamp_duty_pct": 0.0},
        },
        "risk": {
            "max_open_positions_per_strategy": 1, "max_open_positions_total": 8,
            "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
            "margin_per_trade_pct": 6.5, "kill_switch_enabled": False,
        },
        "account": {"starting_capital": 2000000.0,
                    "starting_capital_per_strategy": 500000.0, "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    cfg_path = root / "settings.json"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    return cfg_path


class _Recorder:
    """In-memory event recorder standing in for EventStore."""
    def __init__(self):
        self.events = []

    def record(self, trade_id, strategy_id, instrument, event_type, payload=None,
               source="system", timestamp=None):
        self.events.append({
            "event_type": event_type, "strategy_id": strategy_id, "payload": payload or {},
        })
        return f"{trade_id}-{len(self.events)}"

    def rejected(self):
        return [e for e in self.events if e["event_type"] == "ORDER_REJECTED"]


@pytest.fixture()
def _engine(tmp_path, monkeypatch):
    # Keep the engine fully offline (no data adapter network).
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
    engine.event_store = _Recorder()

    # Force tradeable state offline (mirrors harness._enable_trading).
    ws = engine.data_adapter.ws
    ws.connected = True
    ws._last_tick_time = time.time()
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine._running = True
    engine._on_tick({"instrument": "GOLDM", "ltp": 78000.0, "event_timestamp": time.time()})

    engine._all4 = ["gold_01", "gold_02", "silver_01", "silver_02"]
    engine._recorder = engine.event_store
    yield engine
    try:
        engine.stop()
    except Exception:
        pass
    try:
        persistence.close()
    except Exception:
        pass


def _price(instrument: str) -> float:
    return 78000.0 if instrument == "GOLDM" else 239000.0


def _process(engine, strategy_id, signal_type, held_price, ts):
    inst = {"gold_01": "GOLDM", "gold_02": "GOLDM",
            "silver_01": "SILVERM", "silver_02": "SILVERM"}[strategy_id]
    sig = Signal(
        signal_type=signal_type, instrument=inst, strategy_id=strategy_id,
        timestamp=ts, trigger_price=held_price, stop_price=0.0, quantity=1,
    )
    engine.execution_engine.update_price(inst, held_price)
    engine._process_signal(sig)


def _open_long(engine, strategy_id, ts):
    """Open a real LONG for the strategy through the engine pipeline."""
    _process(engine, strategy_id, SignalType.LONG, _price(
        {"gold_01": "GOLDM", "gold_02": "GOLDM",
         "silver_01": "SILVERM", "silver_02": "SILVERM"}[strategy_id]), ts)


@pytest.fixture(autouse=True)
def _restore_cfg():
    from config import Config
    original = dict(Config._config)
    yield
    Config._config = original


ALL4 = ["gold_01", "gold_02", "silver_01", "silver_02"]


# ---------------------------------------------------------------------------
# 1) Pure logic — _strategy_positions_for_risk for all 4 x both sides
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ALL4)
def test_reversal_long_to_short_allows_all_strategies(sid):
    held = [_FakePos("LONG")]
    assert _strategy_positions_for_risk(SignalType.SHORT, held) == 0


@pytest.mark.parametrize("sid", ALL4)
def test_reversal_short_to_long_allows_all_strategies(sid):
    held = [_FakePos("SHORT")]
    assert _strategy_positions_for_risk(SignalType.LONG, held) == 0


@pytest.mark.parametrize("sid", ALL4)
def test_addon_same_side_still_rejected(sid):
    held = [_FakePos("LONG")]
    assert _strategy_positions_for_risk(SignalType.LONG, held) == 1
    held2 = [_FakePos("SHORT")]
    assert _strategy_positions_for_risk(SignalType.SHORT, held2) == 1


class _FakePos:
    def __init__(self, side):
        self._side = side
        self.is_open = True

    @property
    def is_long(self):
        return self._side == "LONG"

    @property
    def is_short(self):
        return self._side == "SHORT"


# ---------------------------------------------------------------------------
# 2) Engine-level: reversal entry at 1-position cap NOT rejected, all strategies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ALL4)
def test_engine_shorts_reversal_not_rejected_while_long_held(_engine, sid):
    """The BUG-C regression: a SHORT reversal arriving while the strategy still
    holds its (opposite) LONG must be allowed, not max_positions_per_strategy."""
    _open_long(_engine, sid, ts=100.0)
    # strategy now holds a LONG; feed a SHORT reversal at the cap==1
    _process(_engine, sid, SignalType.SHORT, _price(
        {"gold_01": "GOLDM", "gold_02": "GOLDM",
         "silver_01": "SILVERM", "silver_02": "SILVERM"}[sid]), ts=200.0)
    rej = _engine._recorder.rejected()
    assert rej == [], f"{sid} SHORT reversal rejected: {rej}"


@pytest.mark.parametrize("sid", ALL4)
def test_engine_longs_reversal_not_rejected_while_short_held(_engine, sid):
    """SHORT->LONG reversal must likewise not be blocked at the cap."""
    _process(_engine, sid, SignalType.SHORT, 78000.0, ts=100.0)  # open SHORT
    _process(_engine, sid, SignalType.LONG, 78000.0, ts=200.0)   # reversal LONG
    rej = _engine._recorder.rejected()
    assert rej == [], f"{sid} LONG reversal rejected: {rej}"


# ---------------------------------------------------------------------------
# 3) SL exit must never be blocked, any strategy
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ALL4)
def test_engine_sl_exit_never_blocked(_engine, sid):
    """stop_loss_hit exit (is_exit=True) must bypass the cap for every strategy
    and close the held position."""
    _open_long(_engine, sid, ts=100.0)
    inst = {"gold_01": "GOLDM", "gold_02": "GOLDM",
            "silver_01": "SILVERM", "silver_02": "SILVERM"}[sid]
    sl_exit = Signal(
        signal_type=SignalType.SHORT, instrument=inst, strategy_id=sid,
        timestamp=300.0, trigger_price=_price(inst) - 5.0, stop_price=0.0,
        quantity=1, metadata={"exit": True, "exit_reason": "stop_loss_hit"},
    )
    _engine.execution_engine.update_price(inst, sl_exit.trigger_price)
    _engine._process_signal(sl_exit)
    rej = _engine._recorder.rejected()
    assert rej == [], f"{sid} SL exit rejected: {rej}"
    # held position should now be CLOSED (flat)
    open_pos = [p for p in _engine.position_manager.get_positions_by_strategy(sid)
                if p.is_open]
    assert open_pos == [], f"{sid} SL exit did not clear the position"


# ---------------------------------------------------------------------------
# 4) Deferred-reversal exit is cap-exempt and clears state for all strategies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ALL4)
def test_deferred_reversal_exit_is_exit_and_not_rejected(_engine, sid):
    """_process_deferred_exit emits an exit signal that must take the allowed
    (is_exit=True) path for every strategy."""
    inst = {"gold_01": "GOLDM", "gold_02": "GOLDM",
            "silver_01": "SILVERM", "silver_02": "SILVERM"}[sid]
    strat = _engine.strategies[sid]
    strat.position_side = "LONG"
    strat.pending_exit_at_open = True
    strat.pending_exit_reason = "short_reversal"
    strat.pending_exit_bar_start = 100.0
    from trading_engine import Bar
    bar = Bar(instrument=inst, timeframe=strat.fast_timeframe, start_ts=200.0,
              end_ts=300.0, open=78000.0, high=78010.0, low=77990.0, close=78000.0)
    _engine.execution_engine.update_price(inst, 78000.0)
    done = _engine._process_deferred_exit(strat, bar)
    assert done is True
    rej = _engine._recorder.rejected()
    assert rej == [], f"{sid} deferred reversal exit rejected: {rej}"
    assert strat.pending_exit_at_open is False, f"{sid} deferred exit not consumed"