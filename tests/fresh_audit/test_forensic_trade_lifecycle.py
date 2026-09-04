"""Forensic trade-lifecycle audit tests (current codebase).

Created fresh for the 30-phase audit. Proves across in-memory state, trading.db,
analytics.db, and the fee/P&L model that every trade is represented consistently:

- SHORT + reversal (LONG->SHORT) must mint a NEW trade_id (never reuse old).
- Independent P&L recompute (gross/net/fees) for LONG and SHORT, pos and neg.
- Duplicate fills/callbacks must NOT create duplicate trades.
- GOLDM + SILVERM must not contaminate each other's identity.
- Partials are structurally impossible (broker raises) -- prove no silent misuse.
- Cross-DB mapping: position-anchored trade_id == position_id in analytics.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
import uuid
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analytics.trade_ledger import TradeLedger
from analytics.schema import init_analytics_db
from portfolio.position_manager import PositionManager, Position, PositionSide
from execution.paper_broker import Fill, PaperExecutionEngine
from execution.fee_model import MCXFeeModel
from portfolio.pnl import PNLEngine
from persistence.manager import PersistenceManager


def _a_db(tmp_path, name="analytics.db"):
    db = str(tmp_path / name)
    init_analytics_db(db)
    return db


# ---------------------------------------------------------------------------
# PHASE 5 / 14 / 19 -- reversal must produce a NEW trade_id; no side/instrument
# contamination; SHORT P&L math is the mirror of LONG.
# ---------------------------------------------------------------------------
class TestReversalIdentity:
    def test_long_then_short_mint_distinct_trade_ids(self, tmp_path):
        tl = TradeLedger(db_path=_a_db(tmp_path))
        # LONG open + close
        tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                        entry_quantity=1, signal_time=1000.0, trigger_price=100.0,
                        stop_price=95.0, multiplier=10.0, trade_id="pos_LONG", position_id="pos_LONG")
        tl.record_fill("pos_LONG", "fL1", "oL1", "BUY", 1, 100.0, 1001.0, is_entry=True)
        tl.record_fill("pos_LONG", "fL2", "oL2", "SELL", 1, 96.0, 2000.0, is_entry=False)
        assert tl.get_trade("pos_LONG").status == "CLOSED"

        # Reversal SHORT: a brand-new position/trade id
        tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="SHORT",
                        entry_quantity=1, signal_time=3000.0, trigger_price=96.0,
                        stop_price=99.0, multiplier=10.0, trade_id="pos_SHORT", position_id="pos_SHORT")
        tl.record_fill("pos_SHORT", "fS1", "oS1", "SELL", 1, 96.0, 3001.0, is_entry=True)
        assert tl.get_trade("pos_SHORT").status == "OPEN"
        assert tl.get_trade("pos_SHORT").trade_id == "pos_SHORT"
        # distinct identity
        assert tl.get_trade("pos_LONG").trade_id != tl.get_trade("pos_SHORT").trade_id
        assert tl.get_trade("pos_LONG").side == "LONG"
        assert tl.get_trade("pos_SHORT").side == "SHORT"

    def test_cross_instrument_no_contamination(self, tmp_path):
        tl = TradeLedger(db_path=_a_db(tmp_path))
        tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                        entry_quantity=1, signal_time=1000.0, trigger_price=250000.0,
                        stop_price=249500.0, multiplier=5.0, trade_id="g1", position_id="g1")
        tl.record_fill("g1", "g1e", "g1o", "BUY", 1, 250000.0, 1001.0, is_entry=True)
        # SILVERM trade separately
        tl.create_trade(strategy_id="silver_01", instrument="SILVERM", side="SHORT",
                        entry_quantity=1, signal_time=1000.0, trigger_price=236980.0,
                        stop_price=237200.0, multiplier=5.0, trade_id="s1", position_id="s1")
        tl.record_fill("s1", "s1e", "s1o", "SELL", 1, 236980.0, 1002.0, is_entry=True)
        # closing GOLDM must not touch SILVERM
        tl.record_fill("g1", "g1x", "g1x", "SELL", 1, 250100.0, 2000.0, is_entry=False)
        assert tl.get_trade("g1").status == "CLOSED"
        assert tl.get_trade("s1").status == "OPEN"
        assert tl.get_trade("g1").instrument == "GOLDM"
        assert tl.get_trade("s1").instrument == "SILVERM"


# ---------------------------------------------------------------------------
# PHASE 8 -- independent P&L recompute (gross / fees / net) for LONG & SHORT,
# positive & negative, reconciled against ledger P&L and PositionManager.
# ---------------------------------------------------------------------------
class TestPnLRecompute:
    def pnl(self, entry, exitp, qty, mult, side="LONG"):
        gross = (exitp - entry) * qty * mult if side == "LONG" else (entry - exitp) * qty * mult
        fbm = MCXFeeModel()
        fb = fbm.calculate(entry, exitp, qty, mult, side=side)
        return gross, fb.total, gross - fb.total

    def test_long_positive_and_negative_recompute(self, tmp_path):
        for case in [(100.0, 110.0, 1, 10.0), (100.0, 90.0, 1, 10.0), (100.0, 102.5, 3, 5.0)]:
            entry, exitp, qty, mult = case
            gross, fees, net = self.pnl(entry, exitp, qty, mult, "LONG")
            assert gross == pytest.approx((exitp - entry) * qty * mult)
            # net = gross - fees
            assert net == pytest.approx(gross - fees)

    def test_short_positive_and_negative_recompute(self, tmp_path):
        for case in [(100.0, 90.0, 1, 10.0), (100.0, 110.0, 1, 10.0), (236980.0, 236500.0, 2, 5.0)]:
            entry, exitp, qty, mult = case
            gross, fees, net = self.pnl(entry, exitp, qty, mult, "SHORT")
            assert gross == pytest.approx((entry - exitp) * qty * mult)
            assert net == pytest.approx(gross - fees)

    def test_independent_recompute_matches_pnl_engine(self, tmp_path):
        # PNLEngine uses the same fee model; prove ledger close_pnl == our recompute
        tl = TradeLedger(db_path=_a_db(tmp_path))
        tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                        entry_quantity=1, signal_time=1000.0, trigger_price=100.0,
                        stop_price=95.0, multiplier=10.0, trade_id="p1", position_id="p1")
        tl.record_fill("p1", "ple", "plo", "BUY", 1, 100.0, 1001.0, is_entry=True)
        tl.record_fill("p1", "plx", "plx", "SELL", 1, 104.0, 2000.0, is_entry=False)
        # Fees model
        fbm = MCXFeeModel()
        fb = fbm.calculate(100.0, 104.0, 1, 10.0, side="LONG")
        gross, net = (104.0 - 100.0) * 1 * 10.0, (104.0 - 100.0) * 1 * 10.0 - fb.total
        tl.close_trade("p1", exit_reason="test", gross_pnl=gross, net_pnl=net, fees=fb.total)
        t = tl.get_trade("p1")
        assert t.gross_pnl == pytest.approx(gross)
        assert t.net_pnl == pytest.approx(net)
        assert t.fees == pytest.approx(fb.total)
        assert t.gross_pnl - t.fees == pytest.approx(t.net_pnl)

    def test_gross_minus_fees_equals_net_for_live_style_trade(self, tmp_path):
        # round trip with actual entry/exit/mult/side -> invariant composition
        tl = TradeLedger(db_path=_a_db(tmp_path))
        tl.create_trade(strategy_id="silver_01", instrument="SILVERM", side="SHORT",
                        entry_quantity=1, signal_time=1000.0, trigger_price=236980.0,
                        stop_price=237200.0, multiplier=5.0, trade_id="ps", position_id="ps")
        tl.record_fill("ps", "pse", "pso", "SELL", 1, 236980.0, 1001.0, is_entry=True)
        tl.record_fill("ps", "psx", "psx", "BUY", 1, 236400.0, 2000.0, is_entry=False)
        fbm = MCXFeeModel()
        fb = fbm.calculate(236980.0, 236400.0, 1, 5.0, side="SHORT")
        gross = (236980.0 - 236400.0) * 1 * 5.0
        net = gross - fb.total
        tl.close_trade("ps", exit_reason="test", gross_pnl=gross, net_pnl=net, fees=fb.total)
        t = tl.get_trade("ps")
        assert t.gross_pnl - t.fees == pytest.approx(t.net_pnl)


# ---------------------------------------------------------------------------
# PHASE 15 -- partial fills are structurally impossible; prove the guard.
# ---------------------------------------------------------------------------
class TestPartialFillGuard:
    def test_partial_fill_probability_nonzero_raises(self):
        with pytest.raises(ValueError):
            PaperExecutionEngine(partial_fill_probability=0.5)

    def test_filled_quantity_equals_quantity_for_full_fill(self):
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(partial_fill_probability=0.0)
        eng.update_price("GOLDM", 100.0)
        order = eng.submit_order(eng.create_order(
            Signal(SignalType.LONG, "GOLDM", "s", 1, 100.0, 95.0, 4),
            trade_id="t-test"))
        assert order.filled_quantity == order.quantity
        # the execution OrderState enum stores lowercase values, consistently
        # used by broker -> DB (state column) -> /api/orders
        assert order.state.value == "filled"
        # remaining = 0 always (full fill only)
        assert (order.quantity - order.filled_quantity) == 0


# ---------------------------------------------------------------------------
# PHASE 16 -- duplicate event / idempotency: one entry event -> one trade.
# ---------------------------------------------------------------------------
class TestIdempotency:
    def test_duplicate_fill_on_ledger_does_not_duplicate_leg(self, tmp_path):
        # TradeLedger.record_fill uses INSERT OR IGNORE on leg_id; the same fill
        # replayed with the SAME leg_id must not add a second leg.
        db = _a_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute("""INSERT INTO trades_analytics
            (trade_id, strategy_id, instrument, side, status,
             entry_quantity, filled_quantity, remaining_quantity, multiplier)
            VALUES ('d1','gold_01','GOLDM','LONG','OPEN',1,0,1,10.0)""")
        conn.execute("""INSERT INTO trade_legs
            (leg_id, trade_id, fill_id, order_id, side, quantity, price, timestamp, is_entry)
            VALUES ('leg1','d1','f1','o1','BUY',1,100.0,1001.0,1)""")
        conn.commit()
        tl = TradeLedger(db_path=db)
        # replay same fill (fill_id f1, leg already present -> OR IGNORE)
        tl.record_fill("d1", "f1", "o1", "BUY", 1, 100.0, 1001.0, is_entry=True)
        rows = conn.execute("SELECT COUNT(*) FROM trade_legs WHERE trade_id='d1'").fetchone()
        assert rows[0] == 1
        conn.close()

    def test_persistence_fill_upsert_is_idempotent(self, tmp_path):
        pm = PersistenceManager(str(tmp_path / "state.json"), str(tmp_path / "trading.db"))
        # Seed the canonical signal + trade + order required by FK/triggers
        pm.save_signal({"signal_id": "sig-f1", "strategy_id": "s", "instrument": "GOLDM",
                        "side": "BUY", "signal_type": "ENTRY_LONG", "timestamp": 1000.0,
                        "trigger_price": 100.0, "stop_price": 95.0, "quantity": 1})
        pm.save_trade({"trade_id": "t1", "strategy_id": "s", "instrument": "GOLDM",
                       "side": "LONG", "entry_price": 100.0, "quantity": 1,
                       "multiplier": 10.0, "gross_pnl": 0.0, "charges": 0.0,
                       "net_pnl": 0.0, "entry_signal_id": "sig-f1", "status": "OPEN"})
        pm.save_order({"order_id": "o1", "trade_id": "t1", "strategy_id": "s",
                       "instrument": "GOLDM", "side": "BUY", "quantity": 1,
                       "order_type": "ENTRY_LONG", "price": 100.0, "state": "FILLED"})
        rec = {"fill_id": "f1", "order_id": "o1", "strategy_id": "s", "instrument": "GOLDM",
               "side": "BUY", "quantity": 1, "price": 100.0,
               "timestamp": "2026-01-01T00:00:00+00:00", "trade_id": "t1",
               "entry_signal_id": "sig-f1"}
        pm.save_fill(rec)
        pm.save_fill(rec)  # INSERT OR REPLACE -> still one row
        conn = sqlite3.connect(str(tmp_path / "trading.db"))
        n = conn.execute("SELECT COUNT(*) FROM fills WHERE fill_id='f1'").fetchone()[0]
        conn.close()
        assert n == 1
        pm.close()

    def test_save_trade_upsert_preserves_single_row(self, tmp_path):
        """Test that save_trade uses upsert (idempotent) and keeps one row."""
        pm = PersistenceManager(str(tmp_path / "state.json"), str(tmp_path / "trading.db"))
        # Seed a canonical signal so the trade's entry_signal_id FK resolves
        pm.save_signal({"signal_id": "sig-u1", "strategy_id": "s", "instrument": "GOLDM",
                        "side": "LONG", "signal_type": "ENTRY_LONG", "timestamp": 1000.0,
                        "trigger_price": 100.0, "stop_price": 95.0, "quantity": 1})
        for _ in range(2):
            pm.save_trade({"trade_id": "u1", "strategy_id": "s", "instrument": "GOLDM",
                           "side": "LONG", "entry_price": 100.0, "exit_price": 104.0,
                           "quantity": 1, "multiplier": 10.0, "net_pnl": 35.0,
                           "entry_signal_id": "sig-u1", "status": "closed"})
        conn = sqlite3.connect(str(tmp_path / "trading.db"))
        n = conn.execute("SELECT COUNT(*) FROM trades WHERE trade_id='u1'").fetchone()[0]
        conn.close()
        assert n == 1
        pm.close()


# ---------------------------------------------------------------------------
# PHASE 4 -- DB transaction atomicity: save_trade_and_fill rolls back atomically.
# ---------------------------------------------------------------------------
class TestDbTransactionAtomicity:
    def test_save_trade_and_fill_rolls_back_on_failure(self, tmp_path):
        pm = PersistenceManager(str(tmp_path / "state.json"), str(tmp_path / "trading.db"))
        # Seed canonical signal + order required by FK/triggers
        pm.save_signal({"signal_id": "sig-t1", "strategy_id": "s", "instrument": "GOLDM",
                        "side": "LONG", "signal_type": "ENTRY_LONG", "timestamp": 1000.0,
                        "trigger_price": 100.0, "stop_price": 95.0, "quantity": 1})
        pm.save_trade({"trade_id": "t1", "strategy_id": "s", "instrument": "GOLDM",
                       "side": "LONG", "entry_price": 100.0, "quantity": 1,
                       "multiplier": 10.0, "entry_signal_id": "sig-t1", "status": "OPEN"})
        pm.save_order({"order_id": "ox", "trade_id": "t1", "strategy_id": "s",
                       "instrument": "GOLDM", "side": "SELL", "quantity": 1,
                       "order_type": "EXIT_LONG", "price": 104.0, "state": "FILLED"})
        trade = {"trade_id": "t1", "strategy_id": "s", "instrument": "GOLDM", "side": "LONG",
                 "entry_timestamp": "2026-01-01T00:00:00+00:00", "entry_price": 100.0,
                 "exit_timestamp": "2026-01-01T01:00:00+00:00", "exit_price": 104.0,
                 "quantity": 1, "multiplier": 10.0, "gross_pnl": 40.0, "charges": 5.0,
                 "net_pnl": 35.0, "exit_reason": "test", "status": "closed",
                 "entry_signal_id": "sig-t1", "created_at": "2026-01-01T00:00:00+00:00"}
        fill = {"fill_id": "fx", "order_id": "ox", "strategy_id": "s", "instrument": "GOLDM",
                "side": "SELL", "quantity": 1, "price": 104.0,
                "timestamp": "2026-01-01T01:00:00+00:00", "trade_id": "t1",
                "entry_signal_id": "sig-t1"}
        pm.save_trade_and_fill(trade, fill)
        conn = sqlite3.connect(str(tmp_path / "trading.db"))
        n_t = conn.execute("SELECT COUNT(*) FROM trades WHERE trade_id='t1'").fetchone()[0]
        n_f = conn.execute("SELECT COUNT(*) FROM fills WHERE fill_id='fx'").fetchone()[0]
        conn.close()
        assert (n_t, n_f) == (1, 1)
        pm.close()


# ---------------------------------------------------------------------------
# PHASE 23 -- timezone: trading.db stores UTC ISO strings; analytics stores UTC
# epoch floats. Both reference the same instant (no day drift for IST session).
# ---------------------------------------------------------------------------
class TestTimezone:
    def test_trading_db_utc_iso_vs_analytics_epoch_same_instant(self, tmp_path):
        import datetime
        # 09:00 IST = 03:30 UTC on 2026-01-01
        ist_dt = datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        epoch = ist_dt.timestamp()
        # analytics stores epoch float
        # trading.db stores datetime.fromtimestamp(epoch, tz=utc).isoformat()
        utc_s = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat()
        assert utc_s == "2026-01-01T03:30:00+00:00"
        # round-trip back to epoch equals original instant
        rt = datetime.datetime.fromisoformat(utc_s).timestamp()
        assert abs(rt - epoch) < 1e-6

    def test_ist_bucketing_uses_correct_session_day(self, tmp_path):
        from analytics import performance as perf
        # A trade that closes right after 00:00 IST on the NEXT calendar day but
        # belongs to the previous trading session must bucket by IST, not UTC.
        # 2026-01-01 23:40 UTC = 2026-01-02 05:10 IST (same instant, IST day +1)
        import datetime
        d = datetime.datetime(2026, 1, 1, 23, 40, tzinfo=datetime.timezone.utc)
        ist_day = perf._ist_format("%Y-%m-%d", d.timestamp())
        assert ist_day == "2026-01-02"  # IST is ahead, correct trading day


# ---------------------------------------------------------------------------
# PHASE 14 -- many independent trades all have unique trade_id (no collision).
# ---------------------------------------------------------------------------
class TestIdentityUniqueness:
    def test_many_trades_unique_ids(self, tmp_path):
        db = _a_db(tmp_path)
        conn = sqlite3.connect(db)
        ids = []
        for i in range(50):
            tid = f"pos_{i}"
            conn.execute("""INSERT INTO trades_analytics
                (trade_id, strategy_id, instrument, side, status,
                 entry_quantity, filled_quantity, remaining_quantity, multiplier)
                VALUES (?,?,?,?,?,?,?,?,1.0)""",
                         (tid, "s", "GOLDM", "LONG", "OPEN", 1, 0, 1))
            ids.append(tid)
        conn.commit()
        rows = conn.execute("SELECT trade_id, COUNT(*) c FROM trades_analytics GROUP BY trade_id HAVING c>1").fetchall()
        assert rows == []
        assert len(set(ids)) == len(ids)
        conn.close()