"""DEEP PER-STRATEGY VERIFICATION — full lifecycle for EACH of the 4 strategies,
proving the SL-exit ledger fix is correct independently per strategy, and that:

  1) An entry fills -> an OPEN trade lands in analytics.db (1:1, trade_id=position_id)
  2) An SL exit closes that trade and it is PURGED from the open-trades cache
     (the deployed fix, verified independently for every strategy)
  3) The SL exit does NOT auto-generate a new opposite trade: the strategy goes
     flat (or only a future signal arms a NEW pending, never an SL-driven trade).

This isolates the fix's correctness per-strategy rather than as a lumped whole.
"""
import json
import time
from pathlib import Path

import pytest

from analytics.schema import init_analytics_db
from analytics.trade_ledger import TradeLedger
from strategies.types import Signal, SignalType

ALL4 = ["gold_01", "gold_02", "silver_01", "silver_02"]
INST = {"gold_01": "GOLDM", "gold_02": "GOLDM",
        "silver_01": "SILVERM", "silver_02": "SILVERM"}


def _write_config(root: Path) -> Path:
    data = {
        "system": {"name": "Revers4", "version": "1.0.0", "environment": "paper",
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
    analytics_db = str(tmp_path / "data" / "db" / "analytics.db")
    init_analytics_db(analytics_db)

    from persistence.manager import PersistenceManager
    from trading_engine import TradingEngine
    persistence = PersistenceManager(
        state_path=str(tmp_path / "data" / "db" / "system_state.json"),
        db_path=str(tmp_path / "data" / "db" / "trading.db"),
    )
    engine = TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
    # Keep the event_store a real EventStore so TRADE_CLOSED lands in analytics.db
    # (mirrors production). engine.event_store is created in __init__ already.
    engine._analytics_db_path = analytics_db

    from core.market_status import MarketState, EngineStatus
    ws = engine.data_adapter.ws
    ws.connected = True
    ws._last_tick_time = time.time()
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine._running = True
    engine._on_tick({"instrument": "GOLDM", "ltp": 78000.0, "event_timestamp": time.time()})

    # Wire the ATOMIC TradeCloseManager exactly as production start() does, so
    # SL/reversal exits take the real close path (record_fill->close_trade) and
    # not the legacy fallback that skips the analytics ledger.
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
    # The fresh TradingEngine created its own EventStore against analytics.db;
    # used by both the engine and the TradeCloseManager above.
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


def _rejections(engine, sid):
    """ORDER_REJECTED events for a strategy from the real event store."""
    return engine.event_store.get_events_for_strategy(sid, "ORDER_REJECTED", 100)


# ---------------------------------------------------------------------------
# 1) For EACH strategy: entry fills -> exactly ONE matching OPEN trade in
#    analytics.db with trade_id == position_id (1:1 anchoring)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ALL4)
def test_entry_creates_one_open_trade_per_strategy(_engine, sid):
    _open_long(_engine, sid, ts=100.0)
    positions = _engine.position_manager.get_positions_by_strategy(sid)
    open_pos = [p for p in positions if p.is_open]
    assert len(open_pos) == 1, f"{sid}: expected exactly 1 open position, got {len(open_pos)}"

    # Must have created a ledger trade through the same analytics.db path
    tl = _engine.trade_ledger
    open_trades = tl.get_open_trades(strategy_id=sid)
    assert len(open_trades) == 1, f"{sid}: exactly 1 open ledger trade expected, got {len(open_trades)}"
    t = open_trades[0]
    assert t.strategy_id == sid
    assert t.status == "OPEN"
    assert t.trade_id == open_pos[0].position_id, "trade_id must be anchored to position_id"

    # And only ONE trade row for the strategy in analytics.db (no dupes)
    assert tl.count_trades(strategy_id=sid) == 1


@pytest.mark.parametrize("sid", ALL4)
def test_reversal_long_to_short_closes_and_purges_long(_engine, sid):
    """A SHORT reversal while holding a LONG must close+purge the LONG trade
    (cap fix) and NOT create any duplicate / phantom trade. A new entry only
    happens on a later breakout trigger — never from the exit itself."""
    _open_long(_engine, sid, ts=100.0)
    _process(_engine, sid, SignalType.SHORT, _price(INST[sid]), ts=200.0)

    assert _rejections(_engine, sid) == [], f"{sid} SHORT reversal rejected"

    tl = _engine.trade_ledger
    rows = tl.get_trades_for_strategy(sid)
    # Exactly ONE trade row, and it is CLOSED (reversal closed the LONG).
    assert tl.count_trades(strategy_id=sid) == 1, f"{sid}: reversal must not add a phantom trade"
    closed = [t for t in rows if t.status == "CLOSED"]
    assert len(closed) == 1, f"{sid}: the LONG must be CLOSED by reversal"
    # The closed LONG must be purged from the open-trades cache (the fix).
    assert tl.get_open_trades(strategy_id=sid) == [], (
        f"{sid}: closed LONG must be purged from open cache after reversal exit")


# ---------------------------------------------------------------------------
# 2) For EACH strategy: SL exit closes the trade, purges open cache, and
#    does NOT auto-generate a new opposite trade from the SL itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ALL4)
def test_sl_exit_does_not_auto_generate_opposite_trade(_engine, sid):
    """The specific concern: when an SL hits, it must close the held trade and
    NOT itself create a new opposite trade. This verifies per-strategy that the
    SL exit yields exactly one CLOSED trade and no new OPEN trade."""
    inst = INST[sid]
    _open_long(_engine, sid, ts=100.0)
    start_trades = _engine.trade_ledger.count_trades(strategy_id=sid)

    sl_exit = Signal(
        signal_type=SignalType.SHORT, instrument=inst, strategy_id=sid,
        timestamp=300.0, trigger_price=_price(inst) - 5.0, stop_price=0.0,
        quantity=1, metadata={"exit": True, "exit_reason": "stop_loss_hit"},
    )
    _engine.execution_engine.update_price(inst, sl_exit.trigger_price)
    _engine._process_signal(sl_exit)

    assert _rejections(_engine, sid) == [], f"{sid} SL exit rejected"

    # Position fully closed -> flat
    open_pos = [p for p in _engine.position_manager.get_positions_by_strategy(sid) if p.is_open]
    assert open_pos == [], f"{sid}: SL exit must fully close, no position left"

    tl = _engine.trade_ledger
    after_trades = tl.count_trades(strategy_id=sid)
    # SL exit must NOT create a NEW trade. start 1 (entry). If it generated an
    # opposite trade, count would become 2. Must stay 1.
    assert after_trades == start_trades, (
        f"{sid}: SL exit should NOT create a new trade (was {start_trades}, now {after_trades})")

    rows = tl.get_trades_for_strategy(sid)
    assert len(rows) == 1, f"{sid}: exactly 1 trade row after SL"
    closed = [t for t in rows if t.status == "CLOSED"]
    assert len(closed) == 1, f"{sid}: the entry trade must be CLOSED by SL"
    assert closed[0].exit_reason != "open"
    assert closed[0].exit_price is not None, f"{sid}: SL exit price recorded"

    # open-trades cache must be empty for this strategy (fix)
    assert tl.get_open_trades(strategy_id=sid) == [], (
        f"{sid}: closed trade must be purged from open cache (ledger fix)")


@pytest.mark.parametrize("sid", ALL4)
def test_sl_exit_events_recorded_per_strategy(_engine, sid):
    inst = INST[sid]
    _open_long(_engine, sid, ts=100.0)
    sl_exit = Signal(
        signal_type=SignalType.SHORT, instrument=inst, strategy_id=sid,
        timestamp=300.0, trigger_price=_price(inst) - 5.0, stop_price=0.0,
        quantity=1, metadata={"exit": True, "exit_reason": "stop_loss_hit"},
    )
    _engine.execution_engine.update_price(inst, sl_exit.trigger_price)
    _engine._process_signal(sl_exit)

    kinds = {e.get("event_type") for e in _engine.event_store.get_events_for_strategy(sid, None, 100)}
    assert "TRADE_CLOSED" in kinds, (
        f"{sid}: TRADE_CLOSED event missing, got {kinds}")
    assert len(_engine.trade_ledger.get_open_trades(strategy_id=sid)) == 0


# ---------------------------------------------------------------------------
# 3) Live-data parity: the isolated strategy runs reproduce the SAME behavior
#    the live gold trades showed (LONG reversed then SHORT SL'd)
# ---------------------------------------------------------------------------
def test_live_gold_pattern_reproduces_consistent_ledger(_engine):
    """Reproduce gold_01 live pattern: LONG(reversal close) then SHORT(SL close).
    Both round trips must end CLOSED, purged from the open cache, and the SL
    must NOT spawn a third phantom trade."""
    sid = "gold_01"
    inst = "GOLDM"
    # Round trip 1: LONG entry -> LONG reversal-closed by a SHORT exit signal.
    _process(_engine, sid, SignalType.LONG, 150768.0, ts=100.0)
    _process(_engine, sid, SignalType.SHORT, 150717.0, ts=200.0)  # closes the LONG (reversal exit)
    assert _rejections(_engine, sid) == []

    # Round trip 2: fresh SHORT entry (the live pattern then SL'd).
    _process(_engine, sid, SignalType.SHORT, 152856.0, ts=250.0)  # opens a real SHORT
    assert len([p for p in _engine.position_manager.get_positions_by_strategy(sid) if p.is_open]) == 1
    sl_exit = Signal(signal_type=SignalType.LONG, instrument=inst, strategy_id=sid,
                     timestamp=300.0, trigger_price=153501.0, stop_price=0.0,
                     quantity=1, metadata={"exit": True, "exit_reason": "stop_loss_hit"})
    _engine.execution_engine.update_price(inst, 153501.0)
    _engine._process_signal(sl_exit)
    assert _rejections(_engine, sid) == []

    tl = _engine.trade_ledger
    assert tl.get_open_trades(strategy_id=sid) == [], "no open gold trade after SL"
    closed = [t for t in tl.get_trades_for_strategy(sid) if t.status == "CLOSED"]
    assert len(closed) == 2, f"expected 2 closed gold trades (LONG reversed + SHORT SL), got {len(closed)}"
    assert {c.side for c in closed} == {"LONG", "SHORT"}
    # exactly 2 — the SL must never fabricate an opposite third trade
    assert tl.count_trades(strategy_id=sid) == 2