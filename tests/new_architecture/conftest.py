"""Shared fixtures for the new-architecture acceptance suite.

Mirrors the proven offline wiring from tests/fresh_audit/test_mission_acceptance.py:
a fully-wired four-strategy TradingEngine with TradeCloseManager, a mock Dhan
adapter, forced LIVE_TRADING, and a seeded first tick.
"""
from __future__ import annotations

import time

import pytest

from tests.fresh_audit import test_full_deep_architecture as harness
from analytics.schema import init_analytics_db
from core.market_status import MarketState, EngineStatus
from core.trade_close import TradeCloseManager
from persistence.manager import PersistenceManager
from trading_engine import TradingEngine

from ._harness import SIDS, write_config


@pytest.fixture(autouse=True)
def _restore_cfg():
    from config import Config
    original = dict(Config._config)
    yield
    Config._config = original


@pytest.fixture()
def engine_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)

    cfg_path = write_config(tmp_path)
    db_path = str(tmp_path / "data" / "db" / "trading.db")
    init_analytics_db(str(tmp_path / "data" / "db" / "analytics.db"))
    persistence = PersistenceManager(
        state_path=str(tmp_path / "data" / "db" / "system_state.json"),
        db_path=db_path,
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
    yield engine, persistence, str(cfg_path), db_path
    try:
        engine.stop()
    except Exception:
        pass
    try:
        persistence.close()
    except Exception:
        pass


@pytest.fixture()
def engine(engine_ctx):
    return engine_ctx[0]


@pytest.fixture()
def persistence(engine_ctx):
    return engine_ctx[1]


@pytest.fixture()
def config_path(engine_ctx):
    return engine_ctx[2]


@pytest.fixture()
def db_path(engine_ctx):
    return engine_ctx[3]


@pytest.fixture()
def runtimes(engine):
    return {sid: engine.runtimes[sid] for sid in SIDS}