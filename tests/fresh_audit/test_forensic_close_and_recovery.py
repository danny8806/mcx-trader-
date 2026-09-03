"""Forensic full-close and recovery tests (current codebase).

Exercises the REAL full close path with all real collaborators wired together:
PositionManager + PNLEngine + MCXFeeModel + PersistenceManager(trading.db) +
TradeLedger(analytics.db) + TradeCloseManager. Proves:

- closing a position persists ONE closed trade to trading.db with correct
  gross/net/charges and the exit fill.
- the SAME net_pnl is written to analytics.db via close_trade.
- entry + exit fills are BOTH present as legs.
- recoverability: a fresh ledger built on the same analytics.db file reads back
  the closed trade (survives a "process restart").
- the persisted trading.db trade record reconciles with the P&L recompute.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analytics.trade_ledger import TradeLedger
from analytics.schema import init_analytics_db
from portfolio.position_manager import PositionManager, Position, PositionSide
from portfolio.pnl import PNLEngine
from execution.paper_broker import Fill
from execution.fee_model import MCXFeeModel
from persistence.manager import PersistenceManager
from core.trade_close import TradeCloseManager


def _make_engine(tmp_path, side="LONG", entry=100.0, exitp=104.0, qty=1, mult=10.0,
                 strategy="gold_01", instrument="GOLDM"):
    tl_db = str(tmp_path / "analytics.db")
    init_analytics_db(tl_db)
    tl = TradeLedger(db_path=tl_db)
    pm = PersistenceManager(str(tmp_path / "state.json"), str(tmp_path / "trading.db"))
    pnl = PNLEngine(fee_model=MCXFeeModel())
    pmpos = PositionManager()

    pid = "pos_close_1"
    pos = Position(
        position_id=pid, strategy_id=strategy, instrument=instrument,
        side=PositionSide.LONG if side == "LONG" else PositionSide.SHORT,
        quantity=qty, average_entry=entry, entry_timestamp=1001.0,
        entry_fill_ids=[f"{pid}-entry"], multiplier=mult,
    )
    pmpos._positions[pid] = pos
    # ledger already has the OPEN trade + entry leg (as the engine created at open)
    tl.create_trade(strategy_id=strategy, instrument=instrument, side=side,
                    entry_quantity=qty, signal_time=1000.0, trigger_price=entry,
                    stop_price=entry - (2 if side == "LONG" else -2), multiplier=mult,
                    trade_id=pid, position_id=pid)
    tl.record_fill(pid, f"{pid}-entry", f"{pid}-ord", "BUY" if side == "LONG" else "SELL",
                   qty, entry, 1001.0, is_entry=True)

    exit_fill = Fill(fill_id=f"{pid}-exit", order_id=f"{pid}-xe",
                     instrument=instrument, side="SELL" if side == "LONG" else "BUY",
                     quantity=qty, price=exitp, timestamp=2000.0, strategy_id=strategy,
                     multiplier=mult)
    mgr = TradeCloseManager(
        position_manager=pmpos, pnl_engines={strategy: pnl}, account_engines={},
        global_account=None, risk_engine=None, persistence=pm,
        event_store=None, telegram=None, trade_ledger=tl,
    )
    ok = mgr.close_position(fill=exit_fill, position=pos, strategy_id=strategy,
                            multiplier=mult, exit_reason="test_exit")
    return pm, tl, pnl, ok, pid


def _fees(entry, exitp, qty, mult, side):
    return MCXFeeModel().calculate(entry, exitp, qty, mult, side=side).total


class TestFullCloseRoundTrip:
    def test_close_persists_one_closed_trade_to_trading_db(self, tmp_path):
        pm, tl, pnl, ok, pid = _make_engine(tmp_path, side="LONG", entry=100.0, exitp=104.0)
        assert ok is True
        conn = sqlite3.connect(str(tmp_path / "trading.db"))
        trades = conn.execute("SELECT trade_id,status,entry_price,exit_price,gross_pnl,net_pnl,charges,quantity,multiplier FROM trades").fetchall()
        conn.close()
        # the alias columns don't exist; just assert ONE trade row
        rows = trades
        assert len(rows) == 1
        assert rows[0][0] == pid
        assert rows[0][1] == "closed"
        assert rows[0][2] == 100.0
        assert rows[0][3] == 104.0
        # gross = (104-100)*1*10 = 40
        assert rows[0][4] == pytest.approx(40.0)
        # net = gross - fees
        assert rows[0][5] == pytest.approx(40.0 - _fees(100.0, 104.0, 1, 10.0, "LONG"))
        assert rows[0][6] == pytest.approx(_fees(100.0, 104.0, 1, 10.0, "LONG"))

    def test_close_is_position_anchored_and_analytics_closed(self, tmp_path):
        pm, tl, pnl, ok, pid = _make_engine(tmp_path, side="LONG", entry=100.0, exitp=104.0)
        t = tl.get_trade(pid)
        assert t.status == "CLOSED"
        assert t.trade_id == pid

    def test_short_close_persists_correct_pnl(self, tmp_path):
        pm, tl, pnl, ok, pid = _make_engine(tmp_path, side="SHORT", entry=100.0, exitp=96.0)
        assert ok is True
        conn = sqlite3.connect(str(tmp_path / "trading.db"))
        row = conn.execute("SELECT gross_pnl FROM trades WHERE trade_id=?", (pid,)).fetchone()
        conn.close()
        # SHORT: (100-96)*1*10 = 40
        assert row[0] == pytest.approx(40.0)

    def test_trading_db_net_matches_analytics_net(self, tmp_path):
        pm, tl, pnl, ok, pid = _make_engine(tmp_path, side="LONG", entry=100.0, exitp=104.0)
        conn = sqlite3.connect(str(tmp_path / "trading.db"))
        row = conn.execute("SELECT net_pnl, gross_pnl, charges FROM trades WHERE trade_id=?", (pid,)).fetchone()
        conn.close()
        a = tl.get_trade(pid)
        assert row[0] == pytest.approx(a.net_pnl)   # trading.db net == analytics net
        assert row[1] == pytest.approx(a.gross_pnl)  # gross matches
        assert row[2] == pytest.approx(a.fees)       # charges == fees

    def test_entry_and_exit_legs_present(self, tmp_path):
        pm, tl, pnl, ok, pid = _make_engine(tmp_path, side="LONG", entry=100.0, exitp=104.0)
        legs = tl.get_legs_for_trade(pid)
        entries = [l for l in legs if l.is_entry]
        exits = [l for l in legs if not l.is_entry]
        assert len(entries) == 1
        assert len(exits) == 1
        assert entries[0].price == 100.0
        assert exits[0].price == 104.0

    def test_recovery_reads_back_closed_trade_after_new_ledger(self, tmp_path):
        """Phase 17 - 'restart': a fresh TradeLedger on the same analytics.db
        must read back the closed trade (durability / no phantom OPEN)."""
        pm, tl, pnl, ok, pid = _make_engine(tmp_path, side="LONG", entry=100.0, exitp=104.0)
        # simulate restart: new ledger instance, same DB file
        tl2 = TradeLedger(db_path=str(tmp_path / "analytics.db"))
        t = tl2.get_trade(pid)
        assert t is not None
        assert t.status == "CLOSED"
        assert t.net_pnl == pytest.approx(40.0 - _fees(100.0, 104.0, 1, 10.0, "LONG"))


class TestTradeBookReconcile:
    def test_open_trades_absent_from_trading_db_but_present_in_analytics(self, tmp_path):
        """Phase 9 - canonical model: trading.db 'trades' holds ONLY closed
        trades; an open trade lives in analytics.db (status=OPEN) and in-memory
        positions. This is by design -- verify the invariant."""
        tl_db = str(tmp_path / "a2.db")
        init_analytics_db(tl_db)
        tl = TradeLedger(db_path=tl_db)
        tl.create_trade(strategy_id="silver_01", instrument="SILVERM", side="LONG",
                        entry_quantity=1, signal_time=1000.0, trigger_price=236980.0,
                        stop_price=235457.0, multiplier=5.0, trade_id="open1", position_id="open1")
        tl.record_fill("open1", "oe", "oo", "BUY", 1, 236980.0, 1001.0, is_entry=True)
        t = tl.get_trade("open1")
        assert t.status == "OPEN"
        assert t.exit_price is None
        # trading.db has no trades row for it (row created only at close)
        pm = PersistenceManager(str(tmp_path / "s2.json"), str(tmp_path / "t2.db"))
        conn = sqlite3.connect(str(tmp_path / "t2.db"))
        n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close()
        pm.close()
        assert n == 0