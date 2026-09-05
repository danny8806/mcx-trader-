"""Sequential vs parallel logical equivalence (§48, §49).

The deterministic engine must reach the same final state regardless of the
order in which independent strategy events are interleaved. Two identical
scripts with different interleavings must produce identical in-memory position
state and identical persisted DB lineage for every strategy.
"""
import time

from strategies.types import SignalType

from ._harness import SIDS, open_long, positions


def _exit(engine, sid, ts, signal_id):
    engine.strategies[sid].last_exit_reason = "long_reversal"
    price = 78100.0 if sid.startswith("gold") else 239200.0
    sig = __import__("strategies.types", fromlist=["Signal"]).Signal(
        signal_type=SignalType.SHORT, instrument="GOLDM" if sid.startswith("gold") else "SILVERM",
        strategy_id=sid, timestamp=ts, trigger_price=price, stop_price=0.0,
        quantity=1, signal_id=signal_id,
        metadata={"exit": True, "exit_reason": "long_reversal", "exit_price": price})
    engine.execution_engine.update_price("GOLDM" if sid.startswith("gold") else "SILVERM", price)
    engine._process_signal(sig)


def _run_sequential(engine):
    t0 = 1_700_000_000.0
    for i, sid in enumerate(SIDS):
        open_long(engine, sid, t0 + i)
        _exit(engine, sid, t0 + i + 0.1, f"exit-{sid}")


def _run_burst(engine):
    t0 = 1_700_000_000.0
    for i, sid in enumerate(SIDS):
        open_long(engine, sid, t0 + i)
    for i, sid in enumerate(SIDS):
        _exit(engine, sid, t0 + i + 0.1, f"exit-{sid}")


def _snapshot(persistence, sid):
    trades = persistence._db.query(
        "SELECT side, status, exit_reason, entry_signal_id, exit_signal_id "
        "FROM trades WHERE strategy_id=? ORDER BY id", (sid,))
    fills = persistence._db.query(
        "SELECT side, quantity, price, fill_type FROM fills "
        "WHERE strategy_id=? ORDER BY id", (sid,))
    events = persistence._db.query(
        "SELECT event_type, strategy_id FROM events "
        "WHERE strategy_id=? ORDER BY id", (sid,))
    return trades, fills, events


def _build_engine(tmp_path, monkeypatch):
    from tests.fresh_audit import test_full_deep_architecture as harness
    import tempfile
    from pathlib import Path
    from analytics.schema import init_analytics_db
    from core.market_status import MarketState, EngineStatus
    from core.trade_close import TradeCloseManager
    from persistence.manager import PersistenceManager
    from trading_engine import TradingEngine

    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)
    root = Path(tmp_path)
    from ._harness import write_config
    cfg = write_config(root)
    db_path = str(root / "data" / "db" / "trading.db")
    init_analytics_db(str(root / "data" / "db" / "analytics.db"))
    persistence = PersistenceManager(
        state_path=str(root / "data" / "db" / "system_state.json"), db_path=db_path)
    engine = TradingEngine(config_path=str(cfg))
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
    return engine, persistence


def test_equivalent_outcome_across_interleavings(tmp_path, monkeypatch):
    engine_a, persistence_a = _build_engine(tmp_path / "a", monkeypatch)
    engine_b, persistence_b = _build_engine(tmp_path / "b", monkeypatch)

    try:
        _run_sequential(engine_a)
        _run_burst(engine_b)
        for sid in SIDS:
            assert _snapshot(persistence_a, sid) == _snapshot(persistence_b, sid), sid
            assert positions(engine_a, sid) == positions(engine_b, sid), sid
    finally:
        for e in (engine_a, engine_b):
            try:
                e.stop()
            except Exception:
                pass
        for p in (persistence_a, persistence_b):
            try:
                p.close()
            except Exception:
                pass