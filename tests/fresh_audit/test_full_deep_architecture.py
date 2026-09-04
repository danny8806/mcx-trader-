"""Full-depth architecture verification (algo-tester deep pass).

Builds the REAL TradingEngine end-to-end (only the Dhan data adapter is
replaced by a controllable mock) and verifies the complete
signal -> order -> fill -> position -> ledger -> account -> persistence
lifecycle, restart recovery, dedup-across-restart, and deep financial
invariants using independently-coded reference math (never production code
as a reference).

Sections:
  A. Full engine lifecycle: start -> READY -> entry -> exit -> reconcile
  B. Restart recovery: open position survives restart; no EOD force-close —
     positions carry across session/MARKET_CLOSE until a real exit signal
  C. Fill dedup survives restart
  D. Deep financial invariants across multiple round trips
  E. Pure component invariants (candle aggregation, fee/margin math,
     trade-close atomicity on persistence failure)
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BOOTSTRAP_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _BOOTSTRAP_ROOT not in sys.path:
    sys.path.insert(0, _BOOTSTRAP_ROOT)

import pytest

from config import Config
from persistence.manager import PersistenceManager
from execution.paper_broker import Fill
from execution.fee_model import MCXFeeModel
from portfolio.account import AccountEngine
from portfolio.pnl import PNLEngine
from portfolio.position_manager import PositionManager
from core.candle_fetcher import CandleFetcher
from core.risk_engine import RiskEngine
from core.market_status import MarketState, EngineStatus
from core.trade_close import TradeCloseManager
from strategies.types import Signal, SignalType
from analytics.schema import init_analytics_db
from reconciliation.engine import ReconciliationEngine
from trading_engine import TradingEngine

IST = timezone(timedelta(hours=5, minutes=30))

# ═══════════════════════════════════════════════════════════════════════
# Independent reference math (no production code used as reference)
# ═══════════════════════════════════════════════════════════════════════

def indep_gross_long(entry: float, exit: float, qty: int, mult: float) -> float:
    return (exit - entry) * qty * mult


def indep_gross_short(entry: float, exit: float, qty: int, mult: float) -> float:
    return (entry - exit) * qty * mult


def indep_charges(entry: float, exit_price: float, qty: int, mult: float, side: str) -> float:
    """Independent MCX fee calculation for the test config.

    Config charges per side: brokerage 20, stt_sell 0.01%, exchange 0.0026%,
    sebi 0.0001%, gst 18%, stamp 0.0%.
    """
    buy_turnover = entry * qty * mult
    sell_turnover = exit_price * qty * mult
    if side == "SHORT":
        buy_turnover, sell_turnover = sell_turnover, buy_turnover
    brokerage = 20.0 * 2
    stt = sell_turnover * 0.0001
    exchange = (buy_turnover + sell_turnover) * 0.000026
    sebi = (buy_turnover + sell_turnover) * 0.000001
    stamp = buy_turnover * 0.0
    gst = (brokerage + exchange + sebi) * 0.18
    return round(brokerage + stt + exchange + sebi + gst + stamp, 2)


def indep_margin(slope: float, intercept: float, price: float, qty: int) -> float:
    return qty * (slope * price + intercept)


# ═══════════════════════════════════════════════════════════════════════
# Harness: real TradingEngine with a controlled data adapter
# ═══════════════════════════════════════════════════════════════════════

class _MockWS:
    connected = True
    _stats = {"tick": 0}
    _instruments = {}
    _last_tick_time = 0.0

    def is_stale(self) -> bool:
        return False


class MockDhanAdapter:
    """Drop-in DhanDataAdapter: REST candle source returns [] and WS is fake."""

    def __init__(self, client_id="", token_file="", pin="", totp_secret="",
                 on_tick=None, on_status=None, **kwargs):
        self.client_id = client_id
        self._on_tick = on_tick
        self._on_status = on_status
        self.ws = _MockWS()
        self.instruments = {}

    def register_instruments(self, instruments: dict) -> None:
        self.instruments = instruments

    def connect(self) -> None:
        self.ws.connected = True

    def disconnect(self) -> None:
        self.ws.connected = False

    def fetch_historical_candles(self, *args, **kwargs):
        return []


def _write_config(root: Path) -> Path:
    data = {
        "system": {
            "name": "DeepArchTest", "version": "1.0.0", "environment": "paper",
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
                        "quantity": 1, "capital": 300000, "enabled": True},
            "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m",
                          "mid_timeframe": "15m", "htf_timeframe": "1h",
                          "quantity": 1, "capital": 300000, "enabled": True},
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
        "account": {"starting_capital": 600000.0,
                    "starting_capital_per_strategy": 300000.0, "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    cfg_path = root / "settings.json"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    return cfg_path


class _World:
    def __init__(self, root: Path):
        self.root = root
        self.cfg_path = _write_config(root)
        init_analytics_db(str(root / "data" / "db" / "analytics.db"))

    def db_path(self) -> Path:
        return self.root / "data" / "db" / "trading.db"

    def state_path(self) -> Path:
        return self.root / "data" / "db" / "system_state.json"

    def build(self):
        persistence = PersistenceManager(
            state_path=str(self.state_path()),
            db_path=str(self.db_path()),
        )
        engine = TradingEngine(config_path=str(self.cfg_path))
        engine.set_persistence(persistence)
        return engine, persistence


@pytest.fixture(autouse=True)
def _restore_config():
    original = dict(Config._config)
    yield
    Config._config = original


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("trading_engine.DhanDataAdapter", MockDhanAdapter)
    return _World(tmp_path)


def _teardown(engine, persistence) -> None:
    try:
        engine.stop()
    finally:
        try:
            persistence.close()
        except Exception:
            pass


def _enable_trading(engine) -> None:
    """Force the engine into a tradeable state regardless of wall clock."""
    ws = engine.data_adapter.ws
    ws._last_tick_time = time.time()
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine._on_tick({"instrument": "GOLDM", "ltp": 78000.0, "event_timestamp": time.time()})


def _process(engine, signal: Signal) -> None:
    engine.execution_engine.update_price(signal.instrument, signal.trigger_price)
    engine._process_signal(signal)


def _reconcile_result(engine, phase="live"):
    recon = ReconciliationEngine(
        persistence=engine._persistence,
        position_manager=engine.position_manager,
        pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines,
        strategies=engine.strategies,
        order_manager=engine.order_manager,
    )
    return recon.reconcile(phase=phase)


def _readonly_sql(db_path, query: str, *params) -> list[tuple]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# A. Full engine lifecycle
# ═══════════════════════════════════════════════════════════════════════

class TestFullLifecycle:
    def test_start_reaches_ready_with_all_components(self, world):
        engine, persistence = world.build()
        try:
            engine.start()
            assert engine.market_status.engine_status == EngineStatus.READY
            assert engine.candle_fetcher._running
            assert engine.event_store is not None
            assert engine.trade_ledger is not None
            assert "gold_01" in engine.strategies
            assert "silver_01" in engine.strategies
            assert engine._trade_close_manager is not None
            assert not engine.safe_mode.is_active
        finally:
            _teardown(engine, persistence)

    def test_entry_exit_roundtrip_persists_and_reconciles(self, world):
        engine, persistence = world.build()
        try:
            engine.start()
            _enable_trading(engine)

            ts = time.time()
            _process(engine, Signal(SignalType.LONG, "GOLDM", "gold_01", ts, 78000.0, 77000.0, 1))

            # ── Entry visible everywhere ──
            positions = engine.position_manager.open_positions
            assert len(positions) == 1
            pos = positions[0]
            assert pos.is_long and pos.instrument == "GOLDM" and pos.quantity == 1
            assert pos.average_entry == 78000.0
            expected_margin = indep_margin(0.125, 126930.0, 78000.0, 1)
            assert abs(pos.margin - expected_margin) < 1e-6
            acct = engine.account_engines["gold_01"]
            assert abs(acct.used_margin - expected_margin) < 1e-6
            assert abs(engine.account_engine.used_margin - expected_margin) < 1e-6

            # DB: 1 order + 1 fill, fill references the order
            db = engine._persistence.db_path
            orders = _readonly_sql(db, "SELECT order_id, state, side FROM orders")
            fills = _readonly_sql(db, "SELECT fill_id, order_id, price FROM fills")
            assert len(orders) == 1 and len(fills) == 1
            assert orders[0][1] == "filled", \
                f"order state serialized as {orders[0][1]!r}, expected 'filled'"
            assert fills[0][1] == orders[0][0], "fill must reference the persisted order"
            assert abs(fills[0][2] - 78000.0) < 1e-6

            # Ledger: position-anchored open trade
            trade = engine.trade_ledger.get_trade(pos.position_id)
            assert trade is not None and trade.status == "OPEN"
            assert trade.trade_id == pos.position_id
            assert trade.multiplier == 10.0

            # ── Exit ──
            ts2 = time.time()
            _process(engine, Signal(SignalType.SHORT, "GOLDM", "gold_01", ts2, 78200.0, 0.0, 1,
                                    metadata={"exit": True}))

            assert len(engine.position_manager.open_positions) == 0
            assert len(engine.position_manager.closed_positions) == 1
            assert acct.used_margin == 0.0
            assert acct.realized_pnl != 0.0

            gross = indep_gross_long(78000.0, 78200.0, 1, 10.0)
            charges = indep_charges(78000.0, 78200.0, 1, 10.0, "LONG")
            net = gross - charges
            assert abs(acct.charges - charges) < 1e-6
            assert abs(acct.realized_pnl - net) < 1e-6
            assert abs(engine.account_engine.realized_pnl - net) < 1e-6
            assert abs(engine.risk_engine.snapshot().get("daily_pnl", 0) - net) < 1e-6
            pnl_eng = engine.pnl_engines["gold_01"]
            assert pnl_eng.trade_count == 1
            assert abs(pnl_eng.realized_net - net) < 1e-6

            # DB: exit fill persisted, trade row closed with same P&L
            trades = _readonly_sql(
                db, "SELECT trade_id, net_pnl, charges, status FROM trades")
            assert len(trades) == 1
            assert trades[0][0] == pos.position_id
            assert trades[0][3].upper() in ("CLOSED", "closed")
            assert abs(trades[0][1] - net) < 1e-6
            assert abs(trades[0][2] - charges) < 1e-6

            # Ledger: trade closed with authoritative P&L
            trade = engine.trade_ledger.get_trade(pos.position_id)
            assert trade.status == "CLOSED"
            assert abs(trade.net_pnl - net) < 1e-6

            # ── Full reconciliation must be CONSISTENT ──
            result = _reconcile_result(engine)
            assert result.is_consistent, result.errors
        finally:
            _teardown(engine, persistence)


# ═══════════════════════════════════════════════════════════════════════
# B. Restart recovery
# ═══════════════════════════════════════════════════════════════════════

class TestRestartRecovery:
    def test_open_position_survives_restart_and_eod_close_persists(self, world):
        engine1, persistence1 = world.build()
        try:
            engine1.start()
            _enable_trading(engine1)
            ts = time.time()
            _process(engine1, Signal(SignalType.LONG, "GOLDM", "gold_01", ts, 78000.0, 77000.0, 1))
            pos_id = engine1.position_manager.open_positions[0].position_id

            state = engine1.snapshot()
            persistence1.save_state(state)
        finally:
            _teardown(engine1, persistence1)

        engine2, persistence2 = world.build()
        try:
            saved = persistence2.load_state()
            assert saved is not None
            engine2.restore(saved)
            engine2.start()

            # Position restored from snapshot
            open_pos = engine2.position_manager.open_positions
            assert len(open_pos) == 1
            assert open_pos[0].position_id == pos_id
            assert open_pos[0].is_long
            assert open_pos[0].average_entry == 78000.0
            # Startup reconciliation must be consistent after restore
            assert not engine2.safe_mode.is_active, "reconciliation failed after restore"

            # EOD force-close has been REMOVED: open positions must carry
            # across a MARKET_CLOSE boundary with no forced exit and no DB
            # trade write. The position only closes on a real exit signal.
            engine2.execution_engine.update_price("GOLDM", 78500.0)
            engine2.market_status.force_state(MarketState.MARKET_CLOSE)
            # No _execute_eod_close exists anymore; the engine must not close.
            assert len(engine2.position_manager.open_positions) == 1, "position must carry (no EOD close)"
            db = engine2._persistence.db_path
            trades = _readonly_sql(db, "SELECT trade_id, status, exit_reason, net_pnl FROM trades")
            # register_position() persists the OPEN trade; no close has happened yet
            assert len(trades) == 1, "OPEN trade persists from register_position()"
            assert trades[0][1].upper() == "OPEN"
            result = _reconcile_result(engine2)
            assert result.is_consistent, result.errors

            # Now drive a real exit signal (SHORT + exit=True closes the LONG)
            _enable_trading(engine2)
            _process(engine2, Signal(SignalType.SHORT, "GOLDM", "gold_01", time.time(), 78500.0, 77500.0, 1,
                                     metadata={"exit": True}))
            assert len(engine2.position_manager.open_positions) == 0, "position closed by real exit signal"
            trades2 = _readonly_sql(db, "SELECT trade_id, status, exit_reason FROM trades")
            assert len(trades2) == 1
            assert trades2[0][0] == pos_id
            assert trades2[0][1].upper() in ("CLOSED", "closed")
            assert trades2[0][2] != "eod_close", "exit must be a real signal, not EOD close"
            result = _reconcile_result(engine2)
            assert result.is_consistent, result.errors
        finally:
            _teardown(engine2, persistence2)


# ═══════════════════════════════════════════════════════════════════════
# C. Fill dedup across restart
# ═══════════════════════════════════════════════════════════════════════

class TestFillDedupAcrossRestart:
    def test_duplicate_fill_ignored_before_and_after_restart(self, world):
        engine1, persistence1 = world.build()
        try:
            engine1.start()
            _enable_trading(engine1)
            ts = time.time()
            _process(engine1, Signal(SignalType.LONG, "GOLDM", "gold_01", ts, 78000.0, 77000.0, 1))
            entry_fill = engine1.order_manager.execution_engine._fills[-1]

            # Redelivery of the same fill in the same process: ignored
            engine1._on_fill(entry_fill)
            assert len(engine1.position_manager.open_positions) == 1
            fills_db = _readonly_sql(engine1._persistence.db_path, "SELECT count(*) FROM fills")
            assert fills_db[0][0] == 1

            state = engine1.snapshot()
            persistence1.save_state(state)
        finally:
            _teardown(engine1, persistence1)

        engine2, persistence2 = world.build()
        try:
            saved = persistence2.load_state()
            engine2.restore(saved)
            engine2.start()
            assert len(engine2.position_manager.open_positions) == 1

            # The same fill redelivered after restart must be deduped from DB
            engine2._on_fill(entry_fill)
            assert len(engine2.position_manager.open_positions) == 1
            fills_db = _readonly_sql(engine2._persistence.db_path, "SELECT count(*) FROM fills")
            assert fills_db[0][0] == 1
        finally:
            _teardown(engine2, persistence2)


# ═══════════════════════════════════════════════════════════════════════
# D. Deep financial invariants across multiple round trips
# ═══════════════════════════════════════════════════════════════════════

class TestFinancialInvariants:
    def test_multiple_roundtrips_conserve_equity_and_margin(self, world):
        engine, persistence = world.build()
        try:
            engine.start()
            _enable_trading(engine)

            # gold long 78000 -> 78200
            _process(engine, Signal(SignalType.LONG, "GOLDM", "gold_01", time.time(), 78000.0, 77000.0, 1))
            _process(engine, Signal(SignalType.SHORT, "GOLDM", "gold_01", time.time(), 78200.0, 0.0, 1,
                                    metadata={"exit": True}))
            # silver short 95000 -> 94800
            _process(engine, Signal(SignalType.SHORT, "SILVERM", "silver_01", time.time(), 95000.0, 96000.0, 1))
            _process(engine, Signal(SignalType.LONG, "SILVERM", "silver_01", time.time(), 94800.0, 0.0, 1,
                                    metadata={"exit": True}))

            gold_gross = indep_gross_long(78000.0, 78200.0, 1, 10.0)
            gold_charges = indep_charges(78000.0, 78200.0, 1, 10.0, "LONG")
            gold_net = gold_gross - gold_charges
            silver_gross = indep_gross_short(95000.0, 94800.0, 1, 5.0)
            silver_charges = indep_charges(95000.0, 94800.0, 1, 5.0, "SHORT")
            silver_net = silver_gross - silver_charges

            gold_acct = engine.account_engines["gold_01"]
            silver_acct = engine.account_engines["silver_01"]
            assert abs(gold_acct.realized_pnl - gold_net) < 1e-6
            assert abs(silver_acct.realized_pnl - silver_net) < 1e-6
            assert gold_acct.used_margin == 0.0 and silver_acct.used_margin == 0.0

            # Global equity conservation: starting_total + net_1 + net_2
            total = 600000.0
            expected_equity = total + gold_net + silver_net
            assert abs(engine.account_engine.equity - expected_equity) < 1e-6
            assert abs(engine.account_engine.used_margin - 0.0) < 1e-6

            # PNL engines aggregate exactly
            assert engine.pnl_engines["gold_01"].trade_count == 1
            assert engine.pnl_engines["silver_01"].trade_count == 1
            assert abs(engine.pnl_engines["gold_01"].realized_net - gold_net) < 1e-6
            assert abs(engine.pnl_engines["silver_01"].realized_net - silver_net) < 1e-6

            # Ledger closed trades carry the same net P&L
            db = engine._persistence.db_path
            trades = _readonly_sql(db, "SELECT strategy_id, net_pnl, status FROM trades ORDER BY id")
            assert len(trades) == 2
            pnl_by_strat = {t[0]: t[1] for t in trades}
            assert abs(pnl_by_strat["gold_01"] - gold_net) < 1e-6
            assert abs(pnl_by_strat["silver_01"] - silver_net) < 1e-6
            assert all(t[2].upper() in ("CLOSED", "closed") for t in trades)

            # Full reconciliation must be consistent
            result = _reconcile_result(engine)
            assert result.is_consistent, result.errors
        finally:
            _teardown(engine, persistence)


# ═══════════════════════════════════════════════════════════════════════
# E. Pure component invariants (fast, engine-free)
# ═══════════════════════════════════════════════════════════════════════

class TestCandleFetcherDeep:
    def test_15m_native_candle_emitted_as_is_and_dedups(self):
        collected = []

        class _CountingAdapter:
            def __init__(self, candles):
                self.candles = candles
                self.calls = 0

            def fetch_historical_candles(self, *args, **kwargs):
                self.calls += 1
                return self.candles

        # A NATIVE 15m candle (start at :01, real exchange offset — NOT a
        # resampled 5m group). Emitted as-is, no 5m aggregation.
        t0 = int(datetime(2026, 8, 28, 9, 1, tzinfo=IST).timestamp())
        raw = [[t0, 100.0, 105.0, 99.0, 102.0, 30]]
        adapter = _CountingAdapter(raw)
        fetcher = CandleFetcher(
            data_adapter=adapter,
            instruments={"GOLDM": {}},
            on_candle_closed=collected.append,
            session_open="09:00",
            session_close="23:30",
        )
        # At 09:20 the native 15m candle (09:01-09:16) has already closed.
        now = datetime(2026, 8, 28, 9, 20, tzinfo=IST)
        fetcher._check_timeframe("GOLDM", {}, "15m", now)
        assert len(collected) == 1, f"expected 1 native bar, got {len(collected)}"
        bar = collected[0]
        assert bar.open == 100.0 and bar.high == 105.0 and bar.low == 99.0 and bar.close == 102.0
        assert bar.volume == 30
        assert bar.start_ts == t0 and bar.end_ts == t0 + 900
        assert bar.timeframe == "15m"

        # Dedup: a second check on the same (already-fetched) candle must not
        # emit again.
        fetcher._check_timeframe("GOLDM", {}, "15m", now)
        assert len(collected) == 1, "already-fetched native candle must not re-emit"

    def test_15m_native_not_emitted_while_still_forming(self):
        collected = []

        class _A:
            def __init__(self, candles):
                self.candles = candles
                self.calls = 0

            def fetch_historical_candles(self, *args, **kwargs):
                self.calls += 1
                return self.candles

        # Native 15m candle (09:01-09:16) checked at 09:10 -> end NOT elapsed,
        # still forming, must not be emitted as closed.
        t0 = int(datetime(2026, 8, 28, 9, 1, tzinfo=IST).timestamp())
        raw = [[t0, 100.0, 103.0, 99.0, 102.0, 10]]
        adapter = _A(raw)
        fetcher = CandleFetcher(adapter, {"GOLDM": {}}, collected.append,
                                session_open="09:00", session_close="23:30")
        now = datetime(2026, 8, 28, 9, 10, tzinfo=IST)
        fetcher._check_timeframe("GOLDM", {}, "15m", now)
        assert len(collected) == 0, "forming native candle must not be emitted"


class TestMarginAndFeesDeep:
    def test_margin_formula_matches_config_for_both_instruments(self):
        gold = indep_margin(0.125, 126930.0, 80000.0, 1)
        assert abs(gold - (0.125 * 80000.0 + 126930.0)) < 1e-6
        gold_2 = indep_margin(0.125, 126930.0, 80000.0, 2)
        assert abs(gold_2 - 2 * gold) < 1e-6
        silver = indep_margin(0.0625, 142900.0, 95000.0, 1)
        assert abs(silver - (0.0625 * 95000.0 + 142900.0)) < 1e-6

    def test_fee_model_matches_independent_charges(self):
        fees = MCXFeeModel.from_config({
            "brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
            "exchange_pct": 0.0026, "sebi_pct": 0.0001,
            "gst_pct": 18.0, "stamp_duty_pct": 0.0,
        })
        independent = indep_charges(78000.0, 78200.0, 1, 10.0, "LONG")
        assert abs(fees.calculate(78000.0, 78200.0, 1, 10.0, "LONG").total - independent) < 1e-6

        independent_short = indep_charges(95000.0, 94800.0, 1, 5.0, "SHORT")
        assert abs(fees.calculate(95000.0, 94800.0, 1, 5.0, "SHORT").total - independent_short) < 1e-6


class TestTradeCloseAtomicity:
    def _make_position(self):
        pm = PositionManager()
        entry = Fill(fill_id=str(uuid.uuid4()), order_id=str(uuid.uuid4()),
                     instrument="GOLDM", side="BUY", quantity=1, price=78000.0,
                     timestamp=time.time() - 3600, strategy_id="g", multiplier=10.0)
        pos = pm.open_position(fill=entry, multiplier=10.0, stop_price=77000.0,
                               margin=1000.0)
        return pm, pos

    def test_close_returns_false_when_persistence_fails(self):
        class _FailingPersistence:
            db_path = "not-important"

            def save_trade_and_fill(self, *args, **kwargs):
                raise RuntimeError("simulated disk failure")

        pm, pos = self._make_position()
        fee_model = MCXFeeModel.from_config({
            "brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
            "exchange_pct": 0.0026, "sebi_pct": 0.0001,
            "gst_pct": 18.0, "stamp_duty_pct": 0.0,
        })
        pnl_engines = {"g": PNLEngine(fee_model=fee_model)}
        account_engines = {"g": AccountEngine(starting_capital=300000.0)}
        exit_fill = Fill(fill_id=str(uuid.uuid4()), order_id=str(uuid.uuid4()),
                         instrument="GOLDM", side="SELL", quantity=1, price=78200.0,
                         timestamp=time.time(), strategy_id="g", multiplier=10.0)
        manager = TradeCloseManager(
            position_manager=pm,
            pnl_engines=pnl_engines,
            account_engines=account_engines,
            global_account=AccountEngine(starting_capital=300000.0),
            risk_engine=RiskEngine(),
            persistence=_FailingPersistence(),
            event_store=None,
            telegram=None,
            trade_ledger=None,
        )
        ok = manager.close_position(fill=exit_fill, position=pos,
                                    strategy_id="g", multiplier=10.0)
        assert ok is False
        # No in-memory side effects on persistence failure
        assert pos.is_open
        assert pnl_engines["g"].trade_count == 0
        assert account_engines["g"].realized_pnl == 0.0
        assert account_engines["g"].used_margin == 0.0
        assert len(pm.closed_positions) == 0

    def test_close_persists_and_closes_when_db_healthy(self, tmp_path):
        db_path = tmp_path / "trading.db"
        state_path = tmp_path / "state.json"
        persistence = PersistenceManager(str(state_path), str(db_path))
        try:
            pm = PositionManager()
            entry = Fill(fill_id=str(uuid.uuid4()), order_id=str(uuid.uuid4()),
                         instrument="SILVERM", side="SELL", quantity=1, price=95000.0,
                         timestamp=time.time() - 3600, strategy_id="s", multiplier=5.0)
            pos = pm.open_position(fill=entry, multiplier=5.0, stop_price=96000.0,
                                   margin=800.0)
            fee_model = MCXFeeModel.from_config({
                "brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                "gst_pct": 18.0, "stamp_duty_pct": 0.0,
            })
            account = AccountEngine(starting_capital=300000.0)
            manager = TradeCloseManager(
                position_manager=pm,
                pnl_engines={"s": PNLEngine(fee_model=fee_model)},
                account_engines={"s": account},
                global_account=AccountEngine(starting_capital=300000.0),
                risk_engine=RiskEngine(),
                persistence=persistence,
                event_store=None,
                telegram=None,
                trade_ledger=None,
            )
            exit_fill = Fill(fill_id=str(uuid.uuid4()), order_id=str(uuid.uuid4()),
                             instrument="SILVERM", side="BUY", quantity=1, price=94800.0,
                             timestamp=time.time(), strategy_id="s", multiplier=5.0)
            ok = manager.close_position(fill=exit_fill, position=pos,
                                        strategy_id="s", multiplier=5.0)
            assert ok is not False and ok is not None
            assert not pos.is_open
            rows = _readonly_sql(db_path, "SELECT trade_id, status FROM trades")
            assert len(rows) == 1 and rows[0][1] == "closed"

            expected = indep_gross_short(95000.0, 94800.0, 1, 5.0) - indep_charges(
                95000.0, 94800.0, 1, 5.0, "SHORT")
            assert abs(account.realized_pnl - expected) < 1e-6
        finally:
            persistence.close()