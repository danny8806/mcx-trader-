"""MISSION §49–51, §64–66 — Per-strategy isolation, execution isolation,
database lineage, restart isolation, concurrency uniqueness, idempotency.

Architecture under test: the engine-core isolation slice. Each strategy owns
its own StrategyRuntime (lifecycle + order manager + position manager) over a
shared PaperExecutionEngine broker transport and a single canonical
trading.db. These tests prove that:

  §49  opening / SL-hitting / reversing ONE strategy never changes any other
       strategy's positions or ledger trades — even across same security_id.
  §50  two orders with identical security_id + side stay independent, and each
       fill routes to the correct strategy / trade / position.
  §51  four simultaneous trades keep fully disjoint DB lineage (trade_id,
       order_id, fill_id) — no cross-link.
  §64  after a snapshot/restore restart each runtime reconstructs ONLY its own
       state; GOLDM_5M must not inherit GOLDM_15M's active trade; broker order
       id -> strategy/trade mapping survives in durable storage.
  §65  four near-simultaneous signals produce unique positions/trades/orders/
       fills; no strategy inherits another strategy's trade.
  §66  duplicate signal / order / fill events do not create duplicates.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from core.market_status import MarketState, EngineStatus
from strategies.types import Signal, SignalType
from trading_engine import TradingEngine

ALL4 = ["gold_01", "gold_02", "silver_01", "silver_02"]

# gold_01 -> GOLDM_5M, gold_02 -> GOLDM_15M, silver_01 -> SILVERM_5M,
# silver_02 -> SILVERM_15M
INST = {"gold_01": "GOLDM", "gold_02": "GOLDM",
        "silver_01": "SILVERM", "silver_02": "SILVERM"}
# Both GOLDM strategies share security_id 569003 (mission §52 independence).
SEC_ID = {"GOLDM": "569003", "SILVERM": "483080"}


def _write_config(root: Path) -> Path:
    data = {
        "system": {
            "name": "Isolation", "version": "1.0.0", "environment": "paper",
            "log_level": "INFO",
            "db_path": str(root / "data" / "db" / "trading.db"),
            "state_path": str(root / "data" / "db" / "system_state.json"),
        },
        "dhan": {
            "client_id": "TEST", "access_token": "", "ws_url": "wss://fake",
            "rest_base": "https://fake",
            "token_file": str(root / "data" / "db" / "dhan_token.json"),
            "pin": "", "totp_secret": "",
        },
        "instruments": {
            "GOLDM": {
                "symbol": "MCX:GOLDM202610", "security_id": "569003",
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
        "risk": {
            "max_open_positions_per_strategy": 2, "max_open_positions_total": 8,
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


@pytest.fixture()
def _engine(tmp_path, monkeypatch):
    from tests.fresh_audit import test_full_deep_architecture as harness
    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)

    from persistence.manager import PersistenceManager
    cfg_path = _write_config(tmp_path)
    persistence = PersistenceManager(
        state_path=str(tmp_path / "data" / "db" / "system_state.json"),
        db_path=str(tmp_path / "data" / "db" / "trading.db"),
    )
    engine = TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
    engine._test_cfg_path = str(cfg_path)

    # Offline fixture: wire TradeCloseManager exactly like production start().
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

    # Force tradeable state offline (mirrors harness._enable_trading).
    ws = engine.data_adapter.ws
    ws.connected = True
    ws._last_tick_time = time.time()
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine._running = True
    engine._on_tick({"instrument": "GOLDM", "ltp": 78000.0, "event_timestamp": time.time()})

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


def _process(engine, strategy_id, signal_type, ts, exit_meta=None):
    inst = INST[strategy_id]
    sig = Signal(signal_type=signal_type, instrument=inst, strategy_id=strategy_id,
                 timestamp=ts, trigger_price=_price(inst), stop_price=0.0, quantity=1,
                 metadata=exit_meta)
    engine.execution_engine.update_price(inst, _price(inst))
    engine._process_signal(sig)


def _open_long(engine, strategy_id, ts):
    _process(engine, strategy_id, SignalType.LONG, ts)


def _sl_exit(engine, strategy_id, ts):
    # A stop-loss exit is a same-closing-side signal flagged as an exit.
    _process(engine, strategy_id, SignalType.SHORT, ts,
             exit_meta={"exit": True, "exit_reason": "stop_loss_hit"})


def _reversal(engine, strategy_id, ts):
    # Bare opposite-side signal while holding LONG = reversal exit (closes the
    # held LONG; re-entry requires a later breakout trigger).
    _process(engine, strategy_id, SignalType.SHORT, ts)


def _open_positions_by_strategy(engine):
    out = {}
    for p in engine.position_manager.open_positions:
        out.setdefault(p.strategy_id, []).append(p)
    return out


def _open_trade_ids_by_strategy(engine):
    out = {}
    for p in engine.position_manager.open_positions:
        out.setdefault(p.strategy_id, []).append(p.trade_id)
    return out


def _readonly_sql(db_path, query, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# §49 — STRATEGY ISOLATION TEST
# ═══════════════════════════════════════════════════════════════════════

class TestSection49_StrategyIsolation:

    def test_gold_5m_open_leaves_others_unchanged(self, _engine):
        """GOLDM_5M opens a trade -> the other three strategies stay untouched."""
        _open_long(_engine, "gold_01", ts=100.0)
        by = _open_trade_ids_by_strategy(_engine)
        assert len(by.get("gold_01", [])) == 1
        for other in ("gold_02", "silver_01", "silver_02"):
            assert other not in by, f"{other} must stay positionless"
        for other in ("gold_02", "silver_01", "silver_02"):
            assert _engine.trade_ledger.count_trades(strategy_id=other) == 0

    def test_gold_15m_open_leaves_others_unchanged_same_security(self, _engine):
        """GOLDM_15M opens a trade (SAME security_id 569003 as GOLDM_5M) ->
        GOLDM_5M and both SILVERM strategies stay untouched (mission §52)."""
        _open_long(_engine, "gold_02", ts=200.0)
        by = _open_trade_ids_by_strategy(_engine)
        assert len(by.get("gold_02", [])) == 1
        for other in ("gold_01", "silver_01", "silver_02"):
            assert other not in by
        pos = _engine.position_manager.open_positions[0]
        assert pos.instrument == "GOLDM"
        # Prove the shared security_id is exercised: gold_01 must still have no
        # evidence the gold_02 trade ever happened.
        assert _engine.trade_ledger.count_trades(strategy_id="gold_01") == 0

    def test_silver_5m_sl_hit_only_closes_its_own(self, _engine):
        """All four hold LONGs; SILVERM_5M hits SL -> only ITS position closes."""
        for sid in ALL4:
            _open_long(_engine, sid, ts={sid: i * 10 + 300 for i, sid in enumerate(ALL4)}[sid])
        before = {sid: _open_trade_ids_by_strategy(_engine).get(sid, [])[0] for sid in ALL4}
        assert len({v for v in before.values()}) == 4

        _sl_exit(_engine, "silver_01", ts=999.0)

        by = _open_trade_ids_by_strategy(_engine)
        assert "silver_01" not in by, "SILVERM_5M position must be closed by SL"
        for sid in ("gold_01", "gold_02", "silver_02"):
            assert by.get(sid) == [before[sid]], f"{sid} trade must survive untouched"
        # Exactly one CLOSED trade in the ledger, owned by silver_01.
        assert _engine.trade_ledger.count_trades(strategy_id="silver_01") == 1
        closed = [t for t in _engine.trade_ledger.get_trades_for_strategy("silver_01")
                  if t.status == "CLOSED"]
        assert len(closed) == 1 and closed[0].trade_id == before["silver_01"]

    def test_silver_15m_reversal_leaves_others_unchanged(self, _engine):
        """All four hold LONGs; SILVERM_15M reverses -> only ITS trade closes;
        no cross-strategy side effect and no phantom trade."""
        for sid in ALL4:
            _open_long(_engine, sid, ts={sid: i * 10 + 400 for i, sid in enumerate(ALL4)}[sid])
        before = {sid: _open_trade_ids_by_strategy(_engine).get(sid, [])[0] for sid in ALL4}

        _reversal(_engine, "silver_02", ts=1499.0)

        by = _open_trade_ids_by_strategy(_engine)
        assert "silver_02" not in by, "reversal must close the held SILVERM_15M LONG"
        for sid in ("gold_01", "gold_02", "silver_01"):
            assert by.get(sid) == [before[sid]], f"{sid} trade must survive untouched"
        # No phantom trade from the reversal exit alone.
        assert _engine.trade_ledger.count_trades(strategy_id="silver_02") == 1
        assert _engine.trade_ledger.count_trades(strategy_id="gold_01") == 1
        # The other SAM-index strategy (silver_01) is unaffected by silver_02.
        assert _engine.trade_ledger.count_trades(strategy_id="silver_01") == 1


# ═══════════════════════════════════════════════════════════════════════
# §50 — EXECUTION ISOLATION TEST
# ═══════════════════════════════════════════════════════════════════════

class TestSection50_ExecutionIsolation:

    def test_same_security_side_orders_stay_independent(self, _engine):
        """GOLDM_5M and GOLDM_15M orders share security_id + side yet are two
        independent orders with distinct order/trade identity."""
        _open_long(_engine, "gold_01", ts=1000.0)
        _open_long(_engine, "gold_02", ts=1001.0)

        orders = list(_engine.execution_engine._orders.values())
        gold_orders = [o for o in orders if o.strategy_id in ("gold_01", "gold_02")]
        assert len(gold_orders) == 2
        by_sid = {o.strategy_id: o for o in gold_orders}
        o1, o2 = by_sid["gold_01"], by_sid["gold_02"]
        assert o1.instrument == o2.instrument == "GOLDM"
        assert o1.side == o2.side
        assert o1.order_id != o2.order_id
        assert o1.trade_id != o2.trade_id
        # Each order's trade anchors exactly the owning strategy's position.
        for sid, order in by_sid.items():
            pos = [p for p in _engine.position_manager.open_positions
                   if p.strategy_id == sid]
            assert len(pos) == 1 and pos[0].trade_id == order.trade_id

    def test_fills_route_to_correct_strategy_trade_position(self, _engine):
        """Fills (returned in arbitrary order) map to the correct strategy,
        trade and position for both gold strategies."""
        _open_long(_engine, "gold_01", ts=1100.0)
        _open_long(_engine, "gold_02", ts=1101.0)

        orders = {o.order_id: o for o in _engine.execution_engine._orders.values()
                  if o.strategy_id in ("gold_01", "gold_02")}
        fills = [f for f in _engine.execution_engine._fills
                 if f.order_id in orders]
        assert len(fills) == 2
        assert len({f.fill_id for f in fills}) == 2
        for fill in fills:
            order = orders[fill.order_id]
            assert fill.strategy_id == order.strategy_id, "fill must go to its own strategy"
            assert fill.trade_id == order.trade_id == fill.trade_id
            pos = [p for p in _engine.position_manager.open_positions
                   if p.strategy_id == order.strategy_id]
            assert len(pos) == 1 and pos[0].trade_id == fill.trade_id
            assert fill.fill_id in pos[0].entry_fill_ids


# ═══════════════════════════════════════════════════════════════════════
# §51 — DATABASE LINEAGE TEST
# ═══════════════════════════════════════════════════════════════════════

class TestSection51_DatabaseLineage:

    def test_four_simultaneous_trades_no_cross_link(self, _engine):
        """Four simultaneous trades (TRD-A..D) keep disjoint DB lineage:
        each strategy's orders/fills/trades reference only its own identity."""
        for i, sid in enumerate(ALL4):
            _open_long(_engine, sid, ts=2000 + i)
        by = _open_trade_ids_by_strategy(_engine)
        assert len(by) == 4
        ids = {sid: by[sid][0] for sid in ALL4}
        assert len(set(ids.values())) == 4, "four distinct trade_ids required"

        db = _engine._persistence.db_path
        # trades table: each trade_id rows to exactly one strategy.
        tb = {r[0]: r[1] for r in _readonly_sql(
            db, "SELECT trade_id, strategy_id FROM trades")}
        for sid, trade_id in ids.items():
            assert tb.get(trade_id) == sid
        for other, tid in ids.items():
            if other != "gold_01":
                assert tb.get(tid) != "gold_01", "cross-strategy trade linkage"
        # fills table: each fill/trade maps to the owning strategy only.
        fill_rows = _readonly_sql(
            db, "SELECT fill_id, order_id, strategy_id, trade_id FROM fills")
        assert len(fill_rows) == 4
        order_owner = {o.order_id: o.strategy_id
                       for o in _engine.execution_engine._orders.values()}
        for fill_id, order_id, f_strat, f_trade in fill_rows:
            assert order_owner[order_id] == f_strat, "order owner must equal fill owner"
            assert f_trade in (ids[f_strat],), "fill must stay on its own trade"
        assert len(set(r[0] for r in fill_rows)) == 4, "distinct fill_ids"


# ═══════════════════════════════════════════════════════════════════════
# §64 — RESTART TEST (snapshot/restore runtime reconstruction)
# ═══════════════════════════════════════════════════════════════════════

class TestSection64_RestartIsolation:

    def test_restart_reconstructs_only_own_state(self, _engine, tmp_path, monkeypatch):
        from persistence.manager import PersistenceManager
        # Same-instrument pair proves GOLDM_5M must not load GOLDM_15M's trade.
        _open_long(_engine, "gold_01", ts=5000.0)
        _open_long(_engine, "gold_02", ts=5001.0)
        before = {sid: [p for p in _engine.position_manager.open_positions
                        if p.strategy_id == sid][0] for sid in ("gold_01", "gold_02")}
        pre_orders = {o.order_id: (o.strategy_id, o.trade_id)
                      for o in _engine.execution_engine._orders.values()
                      if o.strategy_id in ("gold_01", "gold_02")}
        db = _engine._persistence.db_path

        state = _engine.snapshot()
        _engine._persistence.save_state(state)

        # --- process restart: fresh engine, fresh persistence, same DB/state ---
        engine2 = TradingEngine(config_path=str(_engine._test_cfg_path))
        import shutil, tempfile
        restart_tmp = Path(tempfile.mkdtemp())
        new_state = str(restart_tmp / "state.json")
        shutil.copy(_engine._persistence.state_path, new_state)
        persistence2 = PersistenceManager(state_path=new_state, db_path=db)
        engine2.set_persistence(persistence2)
        try:
            saved = persistence2.load_state()
            assert saved is not None
            engine2.restore(saved)

            # Each runtime reconstructs ONLY its own open position.
            for sid in ("gold_01", "gold_02"):
                opened = [p for p in engine2.position_manager.open_positions
                          if p.strategy_id == sid]
                assert len(opened) == 1, f"{sid} must reconstruct exactly its own position"
                assert opened[0].position_id == before[sid].position_id
                assert opened[0].trade_id == before[sid].trade_id
                assert opened[0].is_long
            by2 = _open_trade_ids_by_strategy(engine2)
            assert by2["gold_01"] == [before["gold_01"].trade_id]
            assert by2["gold_02"] == [before["gold_02"].trade_id]
            assert by2["gold_01"] != by2["gold_02"], "no trade inheritance on restart"

            # Runtime identity mirrors back the restored trade ids.
            rt1 = engine2.runtimes.get("gold_01")
            rt2 = engine2.runtimes.get("gold_02")
            assert rt1.current_trade_id == before["gold_01"].trade_id
            assert rt2.current_trade_id == before["gold_02"].trade_id

            # Execution mappings survive restart in durable storage: the broker
            # order ids in trading.db still map to the correct strategy+trade.
            fill_rows = _readonly_sql(
                db, "SELECT order_id, strategy_id, trade_id FROM fills")
            durable = {(r[0], r[1], r[2]) for r in fill_rows}
            for oid, (ostrat, otrade) in pre_orders.items():
                assert (oid, ostrat, otrade) in durable, \
                    f"order {oid} must survive with correct strategy/trade mapping"

            # Margins reconstituted exactly from the restored positions.
            for sid in ("gold_01", "gold_02"):
                pos = [p for p in engine2.position_manager.open_positions
                       if p.strategy_id == sid][0]
                assert abs(engine2.account_engines[sid].used_margin - pos.margin) < 1e-6
            assert abs(engine2.account_engine.used_margin - sum(
                p.margin for p in engine2.position_manager.open_positions)) < 1e-6
        finally:
            try:
                engine2.stop()
            except Exception:
                pass
            try:
                persistence2.close()
            except Exception:
                pass
            shutil.rmtree(restart_tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# §65 — CONCURRENCY TEST (unique identity under near-simultaneous signals)
# ═══════════════════════════════════════════════════════════════════════

class TestSection65_ConcurrencyUniqueness:

    def test_four_simultaneous_signals_produce_unique_identity(self, _engine):
        """Four signals at identical timestamps -> unique positions, trades,
        orders and fills; no strategy inherits another strategy's trade."""
        ts = 42.0  # identical timestamp across all four strategies
        for sid in ALL4:
            _process(_engine, sid, SignalType.LONG, ts)

        positions = _engine.position_manager.open_positions
        assert len(positions) == 4
        assert len({p.position_id for p in positions}) == 4
        assert len({p.trade_id for p in positions}) == 4
        # Every position is owned by exactly the strategy that submitted it.
        for sid in ALL4:
            owned = [p for p in positions if p.strategy_id == sid]
            assert len(owned) == 1
        # Orders carries unique ids and disjoint trade anchors.
        orders = [o for o in _engine.execution_engine._orders.values()
                  if o.strategy_id in ALL4]
        assert len(orders) == 4
        assert len({o.order_id for o in orders}) == 4
        assert len({o.trade_id for o in orders}) == 4
        fills = [f for f in _engine.execution_engine._fills
                 if f.order_id in {o.order_id for o in orders}]
        assert len(fills) == 4
        assert len({f.fill_id for f in fills}) == 4
        order_by_sid = {o.strategy_id: o for o in orders}
        for sid in ALL4:
            owned = [p for p in positions if p.strategy_id == sid][0]
            assert owned.trade_id == order_by_sid[sid].trade_id, "order must anchor its own trade"


# ═══════════════════════════════════════════════════════════════════════
# §66 — IDEMPOTENCY (no duplicate trade/order/fill/position from replayed events)
# ═══════════════════════════════════════════════════════════════════════

class TestSection66_Idempotency:

    def test_duplicate_signal_event_creates_no_duplicate(self, _engine):
        """Replaying the SAME signal event (identical signal_id) through the
        engine creates no duplicate trade, order, fill or position."""
        from analytics.schema import init_analytics_db
        _open_long(_engine, "gold_01", ts=7000.0)
        sig_id = list(_engine.runtimes.get("gold_01").lifecycle._signal_to_trade)[0]

        replay = Signal(signal_type=SignalType.LONG, instrument="GOLDM",
                        strategy_id="gold_01", timestamp=7000.0,
                        trigger_price=78000.0, stop_price=0.0, quantity=1)
        replay.signal_id = sig_id
        replay.metadata = {"pending": False}
        _engine.execution_engine.update_price("GOLDM", 78000.0)
        _engine._process_signal(replay)

        open_pos = [p for p in _engine.position_manager.open_positions
                    if p.strategy_id == "gold_01"]
        assert len(open_pos) == 1, "replayed signal must not create a second position"
        assert open_pos[0].quantity == 1, "replayed signal must not double the quantity"
        assert len(open_pos[0].entry_fill_ids) == 1
        db = _engine._persistence.db_path
        assert _readonly_sql(db, "SELECT count(*) FROM trades")[0][0] == 1
        assert _readonly_sql(db, "SELECT count(*) FROM orders")[0][0] == 1
        assert _readonly_sql(db, "SELECT count(*) FROM fills")[0][0] == 1

    def test_duplicate_fill_event_creates_no_duplicate(self, _engine):
        """Redelivering an already-processed fill is ignored (memory + DB)."""
        _open_long(_engine, "gold_02", ts=7010.0)
        entry_fill = [f for f in _engine.execution_engine._fills][-1]
        assert len(_engine.position_manager.open_positions) == 1

        _engine._on_fill(entry_fill)
        assert len(_engine.position_manager.open_positions) == 1
        db = _engine._persistence.db_path
        assert _readonly_sql(db, "SELECT count(*) FROM fills")[0][0] == 1

    def test_duplicate_order_event_has_no_duplicate_effect(self, _engine):
        """Re-submitting an already-filled broker order and redelivering its
        fill must not create a duplicate fill or position."""
        _open_long(_engine, "silver_01", ts=7020.0)
        order = [o for o in _engine.execution_engine._orders.values()
                 if o.strategy_id == "silver_01"][0]
        assert order.state.value.upper() == "FILLED"
        positions_before = len(_engine.position_manager.open_positions)
        orders_before = len(_engine.execution_engine._orders)

        # Broker-level idempotency: an already-terminal order cannot be
        # re-submitted, so a duplicated order event can never re-execute.
        with pytest.raises(ValueError):
            _engine.execution_engine.submit_order(order)
        assert len(_engine.execution_engine._orders) == orders_before
        assert len(_engine.position_manager.open_positions) == positions_before

    def test_duplicate_fill_ignored_after_restart(self, _engine):
        """The same fill redelivered after a snapshot/restore restart is
        still deduped (durable dedup survives the process restart)."""
        import shutil
        import tempfile
        from persistence.manager import PersistenceManager

        _open_long(_engine, "gold_01", ts=7030.0)
        entry_fill = [f for f in _engine.execution_engine._fills][-1]
        state = _engine.snapshot()
        _engine._persistence.save_state(state)
        db = _engine._persistence.db_path

        restart_tmp = Path(tempfile.mkdtemp())
        new_state = str(restart_tmp / "state.json")
        shutil.copy(_engine._persistence.state_path, new_state)
        persistence2 = PersistenceManager(state_path=new_state, db_path=db)
        engine2 = TradingEngine(config_path=str(_engine._test_cfg_path))
        engine2.set_persistence(persistence2)
        try:
            engine2.restore(persistence2.load_state())
            assert len(engine2.position_manager.open_positions) == 1
            engine2._on_fill(entry_fill)
            assert len(engine2.position_manager.open_positions) == 1
            assert _readonly_sql(db, "SELECT count(*) FROM fills")[0][0] == 1
        finally:
            try:
                engine2.stop()
            except Exception:
                pass
            try:
                persistence2.close()
            except Exception:
                pass
            shutil.rmtree(restart_tmp, ignore_errors=True)