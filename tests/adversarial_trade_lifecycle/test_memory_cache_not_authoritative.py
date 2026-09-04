"""The in-memory ledger cache is NEVER authoritative.

Regression coverage for the `baa04bef` divergence pattern and the guarded
projection heal:

* ``TradeLedger.record_fill`` must apply exit accounting to the persistent DB
  aggregate even when the trade is absent from the in-memory ``_open_trades``
  cache (never silently drop it).
* Duplicate fills must be idempotent against the DB aggregate (filled_quantity
  / exit_quantity not doubled).
* ``TradeCloseManager`` must rebuild a missing derived projection from the
  canonical trade on close (guarded heal) without inventing a trade_id.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from persistence.database import Database
from analytics.trade_ledger import TradeLedger


def _make_db() -> str:
    db_dir = tempfile.mkdtemp()
    db_path = os.path.join(db_dir, "trading.db")
    Database(db_path).close()
    return db_path


def _open_ledger_with_trade(db_path: str) -> TradeLedger:
    ledger = TradeLedger(db_path=db_path)
    ledger.create_trade(
        strategy_id="gold_01", instrument="GOLDM", side="LONG",
        entry_quantity=1, signal_time=time.time(),
        trigger_price=150000.0, stop_price=149000.0,
        multiplier=10.0,
        trade_id="TRD-CACHE", position_id="POS-CACHE",
    )
    ledger.record_fill(
        trade_id="TRD-CACHE", fill_id="F-ENTRY", order_id="O-ENTRY",
        side="BUY", quantity=1, price=150000.0, timestamp=time.time(),
        is_entry=True,
    )
    return ledger


def test_exit_fill_on_cache_miss_still_closes_db_trade():
    """Exit fill when the open cache misses still closes the DB aggregate.

    This is the `baa04bef` regression: an exit fill must never be dropped just
    because the in-memory cache lost the trade. The memory cache is a mirror of
    the DB, never the source of truth.
    """
    db_path = _make_db()
    _open_ledger_with_trade(db_path)

    # A fresh TradeLedger on the same DB loads the OPEN trade from DB.
    ledger_b = TradeLedger(db_path=db_path)
    assert "TRD-CACHE" in ledger_b._open_trades

    # Simulate the divergence: this instance's cache does not hold the trade
    # (e.g. it was created after construction, or the cache is stale), while
    # the DB still has a valid OPEN aggregate.
    ledger_b._open_trades.pop("TRD-CACHE", None)

    ledger_b.record_fill(
        trade_id="TRD-CACHE", fill_id="F-EXIT", order_id="O-EXIT",
        side="SELL", quantity=1, price=151000.0, timestamp=time.time(),
        is_entry=False,
    )

    trade = ledger_b.get_trade("TRD-CACHE")
    assert trade is not None, "trade must still be resolvable from DB on cache miss"
    assert trade.status == "CLOSED", (
        "exit accounting must be applied even on cache miss"
    )
    assert trade.exit_quantity == 1
    assert trade.exit_price == 151000.0
    assert trade.remaining_quantity == 0
    # Fully closed trade must be evicted from the open cache.
    assert "TRD-CACHE" not in ledger_b._open_trades


def test_duplicate_exit_fill_not_double_applied_on_db_aggregate():
    """Recording the same exit fill_id twice must not double exit_quantity."""
    db_path = _make_db()
    ledger = _open_ledger_with_trade(db_path)

    ledger.record_fill(
        trade_id="TRD-CACHE", fill_id="F-EXIT", order_id="O-EXIT",
        side="SELL", quantity=1, price=151000.0, timestamp=time.time(),
        is_entry=False,
    )
    # Replay the SAME exit fill (e.g. after a crash before the dedup mark).
    ledger.record_fill(
        trade_id="TRD-CACHE", fill_id="F-EXIT", order_id="O-EXIT",
        side="SELL", quantity=1, price=151000.0, timestamp=time.time(),
        is_entry=False,
    )

    trade = ledger.get_trade("TRD-CACHE")
    assert trade is not None
    assert trade.exit_quantity == 1, "duplicate exit fill must be idempotent"
    assert trade.remaining_quantity == 0


def test_duplicate_entry_fill_not_double_applied_on_db_aggregate():
    """Recording the same entry fill_id twice must not double filled_quantity."""
    db_path = _make_db()
    ledger = TradeLedger(db_path=db_path)
    ledger.create_trade(
        strategy_id="gold_01", instrument="GOLDM", side="LONG",
        entry_quantity=2, signal_time=time.time(),
        trigger_price=150000.0, stop_price=149000.0,
        multiplier=10.0,
        trade_id="TRD-2Q", position_id="POS-2Q",
    )
    ledger.record_fill(
        trade_id="TRD-2Q", fill_id="F-ENTRY2", order_id="O-ENTRY2",
        side="BUY", quantity=2, price=150000.0, timestamp=time.time(),
        is_entry=True,
    )
    ledger.record_fill(
        trade_id="TRD-2Q", fill_id="F-ENTRY2", order_id="O-ENTRY2",
        side="BUY", quantity=2, price=150000.0, timestamp=time.time(),
        is_entry=True,
    )
    trade = ledger.get_trade("TRD-2Q")
    assert trade is not None
    assert trade.filled_quantity == 2
    assert trade.remaining_quantity == trade.entry_quantity - trade.filled_quantity


def test_closed_canonical_trade_heals_missing_projection():
    """TradeCloseManager rebuilds a missing derived projection on close.

    Guarded heal: canonical trade exists in DB but the analytics projection is
    missing -> rebuild entirely from canonical data, preserving identity. The
    heal must never invent a trade_id or conflate position_id with trade_id.
    """
    from persistence.manager import PersistenceManager
    from portfolio.position_manager import PositionManager
    from portfolio.pnl import PNLEngine
    from portfolio.account import AccountEngine
    from execution.fee_model import MCXFeeModel
    from execution.paper_broker import Fill
    from core.trade_close import TradeCloseManager
    from core.risk_engine import RiskEngine

    db_dir = tempfile.mkdtemp()
    db_path = os.path.join(db_dir, "trading.db")
    pm = PersistenceManager(
        state_path=os.path.join(db_dir, "state.json"),
        db_path=db_path,
    )
    posmgr = PositionManager()

    entry = Fill("F-HEAL-ENTRY", "O-HEAL-E", "GOLDM", "BUY", 1, 150000.0, time.time(),
                 "gold_01", 10.0, "SIG-HEAL", "TRD-HEAL")
    pos = posmgr.open_position(entry, multiplier=10.0, stop_price=149000.0, margin=100000.0)

    # Seed the canonical lineage in trigger-valid order so save_trade_and_fill
    # (strict integrity triggers) succeeds during close.
    pm.save_signal({
        "signal_id": "SIG-HEAL", "strategy_id": "gold_01",
        "instrument": "GOLDM", "side": "BUY", "signal_type": "LONG",
        "timestamp": time.time(),
    })
    pm.save_trade({
        "trade_id": pos.trade_id, "strategy_id": "gold_01",
        "instrument": "GOLDM", "side": "LONG", "entry_price": 150000.0,
        "quantity": 1, "multiplier": 10.0,
        "entry_signal_id": "SIG-HEAL", "status": "open",
    })
    pm.save_order({
        "order_id": "O-HEAL-E", "strategy_id": "gold_01", "instrument": "GOLDM",
        "side": "BUY", "quantity": 1, "order_type": "MARKET", "state": "filled",
        "filled_quantity": 1, "average_fill_price": 150000.0,
        "trade_id": pos.trade_id,
    })
    pm.save_order({
        "order_id": "O-HEAL-X", "strategy_id": "gold_01", "instrument": "GOLDM",
        "side": "SELL", "quantity": 1, "order_type": "MARKET", "state": "submitted",
        "filled_quantity": 0, "average_fill_price": 0.0,
        "trade_id": pos.trade_id,
    })

    # A ledger with NO projection for this canonical trade.
    ledger = TradeLedger(db_path=db_path)
    assert ledger.get_trade("TRD-HEAL") is None

    tcm = TradeCloseManager(
        position_manager=posmgr,
        pnl_engines={"gold_01": PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))},
        account_engines={"gold_01": AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5)},
        global_account=AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5),
        risk_engine=RiskEngine(), persistence=pm, event_store=None,
        telegram=None, event_callback=None, trade_ledger=ledger,
    )
    exit_fill = Fill("F-HEAL-EXIT", "O-HEAL-X", "GOLDM", "SELL", 1, 151000.0, time.time(),
                     "gold_01", 10.0, "SIG-HEAL", "TRD-HEAL")
    result = tcm.close_position(exit_fill, pos, "gold_01", 10.0, "signal_exit")
    assert result is not False and result is not None

    # Position is closed in memory only if the DB persist (and heal) succeeded
    # together with the subsequent memory close.
    healed = ledger.get_trade("TRD-HEAL")
    assert healed is not None, "guarded heal must rebuild the missing projection"
    assert healed.trade_id == pos.trade_id, "canonical identity must be preserved"
    assert healed.trade_id != healed.position_id, "trade_id and position_id must differ"
    assert healed.status == "CLOSED"
    assert healed.exit_quantity == 1
    assert healed.exit_reason in ("signal_exit", "signal_exit")
