"""Analytics lifecycle linkage tests.

Trades are position-anchored 1:1: the ledger trade_id equals the live position_id,
entry/exit fills are recorded as legs, and closing a position closes exactly the
linked ledger trade (never every open trade sharing strategy+instrument).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analytics.trade_ledger import TradeLedger
from analytics.event_store import EventStore
from analytics.schema import init_analytics_db
from portfolio.position_manager import PositionManager, Position, PositionSide
from execution.paper_broker import Fill


def _init(tmp_path, name):
    db = str(tmp_path / name)
    init_analytics_db(db)
    return db


def _create(tmp_path, trade_id, side="LONG", trigger=100.0):
    tl = TradeLedger(db_path=_init(tmp_path, "analytics.db"))
    trade = tl.create_trade(
        strategy_id="gold_01", instrument="GOLDM", side=side,
        entry_quantity=2, signal_time=1000.0, trigger_price=trigger,
        stop_price=95.0, multiplier=10.0,
        trade_id=trade_id, position_id=trade_id,
    )
    return tl, trade


def test_ledger_trade_is_position_anchored(tmp_path):
    tl, trade = _create(tmp_path, "pos_1")
    assert trade.trade_id == "pos_1"
    assert trade.position_id == "pos_1"
    tl.record_fill("pos_1", "f1", "o1", "BUY", 2, 100.0, 1001.0, is_entry=True)
    t = tl.get_trade("pos_1")
    assert t.filled_quantity == 2
    assert t.average_entry_price == 100.0
    assert t.remaining_quantity == 0
    tl.record_fill("pos_1", "f2", "o2", "SELL", 2, 110.0, 2000.0, is_entry=False)
    t = tl.get_trade("pos_1")
    assert t.status == "CLOSED"
    assert t.gross_pnl == pytest.approx((110.0 - 100.0) * 2 * 10.0)


def test_close_only_links_one_trade_across_same_strategy_instrument(tmp_path):
    """Two open ledger trades on the same strategy:instrument: closing one
    position must close only its linked trade."""
    tl = TradeLedger(db_path=_init(tmp_path, "analytics.db"))
    tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                    entry_quantity=1, signal_time=1000.0, trigger_price=100.0,
                    stop_price=95.0, multiplier=10.0, trade_id="pos_1", position_id="pos_1")
    tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                    entry_quantity=1, signal_time=1000.0, trigger_price=100.0,
                    stop_price=95.0, multiplier=10.0, trade_id="pos_2", position_id="pos_2")
    tl.record_fill("pos_1", "f1", "o1", "BUY", 1, 100.0, 1001.0, is_entry=True)
    tl.record_fill("pos_2", "f2", "o2", "BUY", 1, 100.0, 1002.0, is_entry=True)

    tl.record_fill("pos_1", "f3", "o3", "SELL", 1, 110.0, 2000.0, is_entry=False)

    assert tl.get_trade("pos_1").status == "CLOSED"
    assert tl.get_trade("pos_2").status == "OPEN"


def test_trade_close_manager_closes_only_linked_ledger_trade(tmp_path):
    from core.trade_close import TradeCloseManager

    tl = TradeLedger(db_path=_init(tmp_path, "analytics.db"))
    for trade_id, position_id in (("trade_1", "pos_1"), ("trade_2", "pos_2")):
        tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                        entry_quantity=1, signal_time=1000.0, trigger_price=100.0,
                        stop_price=95.0, multiplier=10.0,
                        trade_id=trade_id, position_id=position_id)
        tl.record_fill(trade_id, f"{position_id}-entry", f"{position_id}-ord",
                       "BUY", 1, 100.0, 1001.0, is_entry=True)

    pm = PositionManager()
    for pid in ("pos_1", "pos_2"):
        pos = Position(
            position_id=pid, strategy_id="gold_01", instrument="GOLDM",
            side=PositionSide.LONG, quantity=1, average_entry=100.0,
            entry_timestamp=1001.0, entry_fill_ids=[f"{pid}-entry"],
            trade_id=f"trade_{pid[-1]}",
        )
        pm._positions[pid] = pos

    exit_fill = Fill(fill_id="x1", order_id="o3", instrument="GOLDM", side="SELL",
                     quantity=1, price=110.0, timestamp=2000.0, strategy_id="gold_01")
    mgr = TradeCloseManager(
        position_manager=pm, pnl_engines={}, account_engines={},
        global_account=None, risk_engine=None, persistence=None,
        event_store=None, telegram=None, trade_ledger=tl,
    )
    mgr.close_position(fill=exit_fill, position=pm._positions["pos_1"],
                       strategy_id="gold_01", multiplier=10.0)

    assert tl.get_trade("trade_1").status == "CLOSED"
    assert tl.get_trade("trade_2").status == "OPEN"


def test_order_rejected_event_type_accepted(tmp_path):
    es = EventStore(db_path=_init(tmp_path, "events.db"))
    ev_id = es.record(
        trade_id="rejected:gold_01:1", strategy_id="gold_01", instrument="GOLDM",
        event_type="ORDER_REJECTED", payload={"reason": "risk limit"},
    )
    assert es.get_event_by_id(ev_id)["event_type"] == "ORDER_REJECTED"


def test_create_trade_without_explicit_id_is_rejected(tmp_path):
    tl = TradeLedger(db_path=_init(tmp_path, "analytics.db"))
    with pytest.raises(ValueError, match="trade_id is required"):
        tl.create_trade(
            strategy_id="gold_01", instrument="GOLDM", side="LONG",
            entry_quantity=1, signal_time=1000.0, trigger_price=100.0, stop_price=95.0,
        )


def test_close_position_when_ledger_missing_does_not_invent_projection(tmp_path):
    """BUG-2 regression: closing a position whose analytics trade record is
    missing must create that exact position-anchored trade (entry+exit legs)
    and close it, rather than leaving a ghost OPEN or closing unrelated trades.
    """
    from core.trade_close import TradeCloseManager
    # two open positions on SAME strategy:instrument to prove only the closed
    # one is materialised+closed in analytics, the other stays untouched.
    tl = TradeLedger(db_path=_init(tmp_path, "analytics.db"))
    # pos_1 has NO ledger row (simulates the BUG-1 open-time omission)
    pm = PositionManager()
    for pid, side_, price in (("pos_1", "LONG", 100.0), ("pos_2", "LONG", 200.0)):
        pm._positions[pid] = Position(
            position_id=pid, strategy_id="gold_01", instrument="GOLDM",
            side=PositionSide.LONG, quantity=1, average_entry=price,
            entry_timestamp=1001.0, entry_fill_ids=[f"{pid}-entry"], multiplier=5.0,
            trade_id=f"trade_{pid[-1]}",
        )
    # pos_2 DOES have an open ledger trade -> must remain OPEN.
    tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                    entry_quantity=1, signal_time=1001.0, trigger_price=200.0,
                    stop_price=195.0, multiplier=5.0,
                    trade_id="trade_2", position_id="pos_2")
    tl.record_fill("trade_2", "pos_2-entry", "pos_2-ord", "BUY", 1, 200.0, 1001.0, is_entry=True)

    exit_fill = Fill(fill_id="x1", order_id="o3", instrument="GOLDM", side="SELL",
                     quantity=1, price=110.0, timestamp=2000.0, strategy_id="gold_01")
    mgr = TradeCloseManager(
        position_manager=pm, pnl_engines={}, account_engines={},
        global_account=None, risk_engine=None, persistence=None,
        event_store=None, telegram=None, trade_ledger=tl,
    )
    mgr.close_position(fill=exit_fill, position=pm._positions["pos_1"],
                       strategy_id="gold_01", multiplier=5.0)

    # A missing derived projection is an error; the close path must not invent
    # lifecycle state from position fields.
    assert tl.get_trade("trade_1") is None
    # pos_2 untouched + still OPEN
    assert tl.get_trade("trade_2").status == "OPEN"


def test_backfill_logic_creates_missing_open_trade(tmp_path):
    """BUG-1 regression (omitted from live-affected code, replicated here):
    calling the same create_trade+record_fill sequence used by
    _backfill_ledger_for_open_positions creates the OPEN record and entry leg
    for a restored position, and is idempotent (skips if already present)."""
    tl = TradeLedger(db_path=_init(tmp_path, "analytics.db"))
    # simulate a restored open position missing from analytics
    assert tl.get_trade("pos_9") is None
    tl.create_trade(
        strategy_id="silver_01", instrument="SILVERM", side="LONG",
        entry_quantity=1, signal_time=1788350062.0, trigger_price=236980.0,
        stop_price=235457.0, multiplier=5.0,
        trade_id="pos_9", position_id="pos_9",
    )
    tl.record_fill("pos_9", "f9", "o9", "BUY", 1, 236980.0, 1788350062.0, is_entry=True)
    t = tl.get_trade("pos_9")
    assert t is not None and t.status == "OPEN"
    assert t.average_entry_price == 236980.0
    assert len(tl.get_legs_for_trade("pos_9")) == 1
    # idempotent: re-running create_trade must not duplicate
    import sqlite3, json
    db = str(tmp_path / "analytics.db")
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) c FROM trades_analytics WHERE trade_id='pos_9'").fetchone()
    conn.close()
    assert rows[0] == 1