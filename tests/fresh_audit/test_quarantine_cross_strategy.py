"""MISSION §34 — cross-strategy quarantine.

Acceptance:
  §34  A fill belonging to GOLDM_5M/TRD-A must NEVER be accepted into
       GOLDM_15M/TRD-B (and vice-versa).
  §34  Every lifecycle event is validated against its owning strategy scope:
       a foreign trade_id is rejected/quarantined, logged as ERROR, counted,
       and the lifecycle state is NOT mutated.
  §34  The engine quarantines fills whose strategy cannot be resolved or whose
       trade is out of scope (previously RuntimeError) — no mutation, no crash.

Engine parity: normal fills still route and apply exactly as before through the
BrokerEventRouter (explicit mapping), so the quarantine layer is additive.
"""
import time

import pytest

from core.lifecycle import TradeLifecycleManager
from strategies.types import Signal, SignalType
from execution.broker_router import BrokerEventRouter
from execution.paper_broker import Fill


def _signal(strategy_id, signal_type=SignalType.LONG, instrument="GOLDM",
            trigger=100.0, timestamp=None, signal_id="sig-default"):
    return Signal(
        signal_type=signal_type, instrument=instrument, strategy_id=strategy_id,
        timestamp=timestamp or time.time(), trigger_price=trigger,
        stop_price=0.0, quantity=1, signal_id=signal_id,
    )


# ── lifecycle-level §34 ───────────────────────────────────────────────────

def _scoped_manager(strategy_id="gold_01"):
    return TradeLifecycleManager(strategy_id=strategy_id)


def test_lifecycle_in_scope_trade_created_normally():
    mgr = _scoped_manager("gold_01")
    trade = mgr.create_trade_from_signal(
        _signal("gold_01"), "gold_01", "Gold 01", "GOLDM", 1, 1.0)
    assert trade is not None and trade.trade_id
    assert trade.strategy_id == "gold_01"
    assert mgr.quarantine_count == 0


def test_lifecycle_create_trade_cross_strategy_quarantined():
    """gold_01's lifecycle must refuse to birth a gold_02 trade."""
    mgr = _scoped_manager("gold_01")
    before = mgr.quarantine_count
    result = mgr.create_trade_from_signal(
        _signal("gold_02"), "gold_02", "Gold 02", "GOLDM", 1, 1.0)
    assert result is None
    assert mgr.quarantine_count == before + 1
    assert not mgr._trades  # nothing created


@pytest.mark.parametrize("fn", [
    lambda mgr: mgr.register_pending_order("T-FOREIGN", "p9"),
    lambda mgr: mgr.register_order("T-FOREIGN", "o9", "ENTRY"),
    lambda mgr: mgr.register_entry_fill("T-FOREIGN", "f9", 100.0, 0.0),
    lambda mgr: mgr.register_exit_fill("T-FOREIGN", "f9", 100.0, 0.0,
                                       "s9", "SL", "stop_loss_hit"),
    lambda mgr: mgr.register_position("T-FOREIGN", "pz9"),
    lambda mgr: mgr.close_trade("T-FOREIGN", 0.0, 0.0, 0.0),
])
def test_lifecycle_foreign_trade_id_quarantined_never_mutates(fn):
    mgr = _scoped_manager("gold_01")
    own = mgr.create_trade_from_signal(
        _signal("gold_01"), "gold_01", "Gold 01", "GOLDM", 1, 1.0)
    assert own is not None
    before = mgr.quarantine_count
    # Every lifecycle register/close with a foreign trade_id is rejected.
    result = fn(mgr)
    assert result is False or result is None
    assert mgr.quarantine_count == before + 1
    # The in-scope trade is untouched.
    assert not own.entry_fill_id
    assert not own.entry_order_id
    assert not own.pending_order_id


def test_lifecycle_foreign_id_does_not_break_in_scope_path():
    mgr = _scoped_manager("gold_01")
    own = mgr.create_trade_from_signal(
        _signal("gold_01"), "gold_01", "Gold 01", "GOLDM", 1, 1.0)
    # Force an out-of-scope quarantine first…
    mgr.register_order("T-FOREIGN", "o9")
    assert mgr.quarantine_count == 1
    # …then the genuine in-scope path still works end-to-end.
    assert mgr.register_order(own.trade_id, "o1", "ENTRY") is True
    assert mgr.register_entry_fill(own.trade_id, "f1", 100.0, time.time()) is True
    assert mgr.register_position(own.trade_id, "p1") is True
    assert mgr.register_exit_fill(own.trade_id, "f2", 110.0, time.time()) is True
    assert mgr.close_trade(own.trade_id, 10.0, 1.0, 9.0) is True
    assert own.entry_fill_id == "f1"


# ── engine-level §34 + broker-router integration ──────────────────────────

def _write_config(root):
    import json
    from pathlib import Path
    data = {
        "system": {"name": "ReversAll", "version": "1.0.0", "environment": "paper",
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
            "gold_01": {"instrument": "GOLDM", "fast_timeframe": "5m",
                        "mid_timeframe": "15m", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 500000, "enabled": True},
            "gold_02": {"instrument": "GOLDM", "fast_timeframe": "15m",
                        "mid_timeframe": "1h", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 500000, "enabled": True},
            "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m",
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
        "risk": {"max_open_positions_per_strategy": 1, "max_open_positions_total": 8,
                 "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": False},
        "account": {"starting_capital": 2000000.0,
                    "starting_capital_per_strategy": 500000.0, "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    cfg_path = root / "settings.json"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    return cfg_path


@pytest.fixture(autouse=True)
def _restore_cfg():
    from config import Config
    original = dict(Config._config)
    yield
    Config._config = original


@pytest.fixture()
def _engine(tmp_path, monkeypatch):
    from tests.fresh_audit import test_full_deep_architecture as harness
    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)
    from analytics.schema import init_analytics_db
    from core.market_status import MarketState, EngineStatus
    from core.trade_close import TradeCloseManager
    from persistence.manager import PersistenceManager
    from trading_engine import TradingEngine
    cfg_path = _write_config(tmp_path)
    init_analytics_db(str(tmp_path / "data" / "db" / "analytics.db"))
    persistence = PersistenceManager(
        state_path=str(tmp_path / "data" / "db" / "system_state.json"),
        db_path=str(tmp_path / "data" / "db" / "trading.db"),
    )
    engine = TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
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
    ws = engine.data_adapter.ws
    ws.connected = True
    ws._last_tick_time = time.time()
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine._running = True
    engine._on_tick({"instrument": "GOLDM", "ltp": 78000.0, "event_timestamp": time.time()})
    yield engine, persistence
    try:
        engine.stop()
    except Exception:
        pass
    try:
        persistence.close()
    except Exception:
        pass


def _process(engine, strategy_id, signal_type, held_price, ts):
    inst = {"gold_01": "GOLDM", "gold_02": "GOLDM",
            "silver_01": "SILVERM", "silver_02": "SILVERM"}[strategy_id]
    sig = _signal(strategy_id, signal_type=signal_type, instrument=inst,
                  trigger=held_price, timestamp=ts, signal_id=f"sig-{strategy_id}-{int(ts * 1000) % 10**6}")
    engine.execution_engine.update_price(inst, held_price)
    engine._process_signal(sig)


def _open_long(engine, strategy_id, ts):
    price = 78000.0 if strategy_id.startswith("gold") else 239000.0
    _process(engine, strategy_id, SignalType.LONG, price, ts)


def _positions(engine, strategy_id):
    return engine.position_manager.get_positions_by_strategy(strategy_id)


def _make_fill(order_id, strategy_id, trade_id, price=100.0, fill_id=None):
    return Fill(
        fill_id=fill_id or f"f-{order_id}", order_id=order_id, instrument="GOLDM",
        side="BUY", quantity=1, price=price, timestamp=time.time(),
        strategy_id=strategy_id, trade_id=trade_id,
    )


def _quarantine_reasons(engine):
    return [e["reason"] for e in engine.quarantine_snapshot()["events"]]


def _router_quarantine_reasons(engine):
    return [e["reason"] for e in engine.broker_router._quarantined_events]


def test_engine_cross_strategy_fill_quarantined_never_applied(_engine):
    """A gold_01 order_id hijacked by a gold_02 fill is quarantined."""
    engine, _ = _engine
    _open_long(engine, "gold_01", time.time())
    order_id = next(iter(engine.broker_router.snapshot()["mappings"]))
    assert len(_positions(engine, "gold_01")) == 1

    hijack = _make_fill(order_id, "gold_02", "T-NOMATCH")
    assert engine.broker_router.route_fill(
        hijack, engine._handle_fill, entry_signal_id="s-x", is_exit=False) is False
    assert "fill_strategy_mismatch" in _router_quarantine_reasons(engine)
    # No mutation: gold_01 still has exactly its 1 organic position.
    assert len(_positions(engine, "gold_01")) == 1
    assert len(_positions(engine, "gold_02")) == 0


def test_engine_unknown_strategy_fill_quarantined(_engine):
    """Fills for strategies that do not exist are quarantined, never applied."""
    engine, _ = _engine
    ghost = _make_fill("o-ghost", "ghost_strategy", "t-ghost")
    engine._handle_fill(ghost, None, None)
    assert "fill_unknown_strategy" in _quarantine_reasons(engine)
    assert engine.quarantine_snapshot()["count"] >= 1


def test_engine_entry_fill_without_resolvable_trade_quarantined(_engine):
    """The old 'no explicit trade reference' RuntimeError is now a quarantine."""
    engine, _ = _engine
    engine.broker_router.register_from_kwargs(
        "o-unres", "o-unres", "T-NOMAD", "gold_01", "GOLDM")
    f = _make_fill("o-unres", "gold_01", "T-NOMAD")
    assert engine.broker_router.route_fill(
        f, engine._handle_fill, entry_signal_id="s-nope", is_exit=False) is True
    assert "entry_fill_no_trade_or_mismatch" in _quarantine_reasons(engine)
    assert len(_positions(engine, "gold_01")) == 0


def test_engine_other_strategy_trade_never_accepted(_engine):
    """§34 core: a gold_02 trade must never be applied on a gold_01 fill."""
    engine, _ = _engine
    _open_long(engine, "gold_02", time.time())
    trade_id = _positions(engine, "gold_02")[0].trade_id
    assert trade_id

    # Mapping claims gold_01 owns a trade that actually belongs to gold_02.
    engine.broker_router.register_from_kwargs(
        "o-hijack", "o-hijack", trade_id, "gold_01", "GOLDM")
    assert engine.broker_router.resolve("o-hijack").strategy_id == "gold_01"

    hijack = _make_fill("o-hijack", "gold_01", trade_id)
    # Router strategy check passes (fill says gold_01 == mapping); the engine
    # must still quarantine because the trade lives in gold_02's scope.
    assert engine.broker_router.route_fill(
        hijack, engine._handle_fill, entry_signal_id="s-x", is_exit=False) is True
    assert "entry_fill_no_trade_or_mismatch" in _quarantine_reasons(engine)
    # gold_01 was NOT opened; gold_02's trade/position is untouched.
    assert len(_positions(engine, "gold_01")) == 0
    assert len(_positions(engine, "gold_02")) == 1


def test_engine_normal_fill_path_unchanged_and_precise(_engine):
    """Parity: normal fills still route to exactly the right strategy."""
    engine, _ = _engine
    _open_long(engine, "gold_01", time.time())
    _open_long(engine, "gold_02", time.time() + 1)
    assert len(_positions(engine, "gold_01")) == 1
    assert len(_positions(engine, "gold_02")) == 1
    # Explicit mappings exist per placed order and both routed.
    assert engine.broker_router.mapping_count == 2
    assert engine.broker_router.routed_count == 2
    assert engine.quarantine_snapshot()["count"] == 0


def test_engine_mappings_restore_from_db_after_restart(_engine):
    """§40: engine-scoped mappings survive a restart via canonical persistence."""
    engine, persistence = _engine
    _open_long(engine, "gold_01", time.time())
    fresh = BrokerEventRouter(persistence=persistence)
    assert fresh.restore() >= 1
    order_id = next(iter(engine.broker_router.snapshot()["mappings"]))
    assert fresh.resolve_strategy(order_id) == "gold_01"