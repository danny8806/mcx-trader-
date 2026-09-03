"""Regression: a fully-closed trade must be removed from the open-trades
cache so get_open_trades() never reports a CLOSED round trip as OPEN.

Root cause found: _update_exit_fill marked status=CLOSED in the DB and
computed P&L but did NOT delete the trade from self._open_trades (unlike
close_trade).  Live effect: after an SL / reversal exit fill the analytics
DB row is CLOSED yet /api/analytics/open-trades (read from _open_trades)
kept showing the trade as OPEN — a DB-vs-memory desync across entry/exit/SL.
"""
import pytest
from analytics.trade_ledger import TradeLedger
from analytics.schema import init_analytics_db


def _ledger(tmp_path, name="analytics.db"):
    db = str(tmp_path / name)
    init_analytics_db(db)
    return TradeLedger(db_path=db)


def _fill_round_trip(tl, trade_id, side, entry, exit_price, qty=1):
    tl.create_trade(
        strategy_id="gold_01", instrument="GOLDM", side=side,
        entry_quantity=qty, signal_time=1000.0, trigger_price=entry,
        stop_price=95.0 if side == "LONG" else 105.0, multiplier=10.0,
        trade_id=trade_id, position_id=trade_id,
    )
    tl.record_fill(trade_id, f"{trade_id}-e", f"{trade_id}-oe", "BUY" if side == "LONG" else "SELL",
                   qty, entry, 1001.0, is_entry=True)
    tl.record_fill(trade_id, f"{trade_id}-x", f"{trade_id}-ox", "SELL" if side == "LONG" else "BUY",
                   qty, exit_price, 2000.0, is_entry=False)


def test_sl_exit_fill_purges_open_trades_cache(tmp_path):
    """BUG: SL full-exit fill marks CLOSED but must vanish from get_open_trades."""
    tl = _ledger(tmp_path)
    _fill_round_trip(tl, "gold_short_sl", "SHORT", entry=100.0, exit_price=112.0)

    t = tl.get_trade("gold_short_sl")
    assert t.status == "CLOSED"
    assert t.exit_reason != "open"

    open_ids = [tr.trade_id for tr in tl.get_open_trades()]
    assert "gold_short_sl" not in open_ids, "CLOSED trade must not be reported as OPEN"


def test_closed_by_exit_fill_matches_close_trade_cache_behavior(tmp_path):
    tl = _ledger(tmp_path)
    _fill_round_trip(tl, "t1", "LONG", entry=100.0, exit_price=110.0)
    assert tl.get_trade("t1").status == "CLOSED"
    assert len(tl.get_open_trades()) == 0


def test_open_trade_remains_open_before_exit(tmp_path):
    tl = _ledger(tmp_path)
    tl.create_trade(strategy_id="silver_01", instrument="SILVERM", side="LONG",
                    entry_quantity=1, signal_time=1000.0, trigger_price=100.0,
                    stop_price=95.0, multiplier=10.0, trade_id="silver_open", position_id="silver_open")
    tl.record_fill("silver_open", "se", "soe", "BUY", 1, 100.0, 1001.0, is_entry=True)
    open_ids = [tr.trade_id for tr in tl.get_open_trades()]
    assert "silver_open" in open_ids
    assert len(open_ids) == 1


def test_two_by_two_only_closed_one_purged(tmp_path):
    """Multiple open trades: closing one via exit fill must purge only that one."""
    tl = _ledger(tmp_path)
    for pid in ("pos_1", "pos_2"):
        tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side="LONG",
                        entry_quantity=1, signal_time=1000.0, trigger_price=100.0,
                        stop_price=95.0, multiplier=10.0, trade_id=pid, position_id=pid)
        tl.record_fill(pid, f"{pid}-e", f"{pid}-oe", "BUY", 1, 100.0, 1001.0, is_entry=True)
    # close only pos_1
    tl.record_fill("pos_1", "pos_1-x", "pos_1-ox", "SELL", 1, 110.0, 2000.0, is_entry=False)
    open_ids = {tr.trade_id for tr in tl.get_open_trades()}
    assert "pos_1" not in open_ids
    assert open_ids == {"pos_2"}


def test_closed_trade_still_queryable_by_get_trade(tmp_path):
    """Purging the OPEN cache must not make a closed trade unqueryable."""
    tl = _ledger(tmp_path)
    _fill_round_trip(tl, "gold_long_exit", "LONG", entry=100.0, exit_price=90.0)
    t = tl.get_trade("gold_long_exit")
    assert t is not None
    assert t.status == "CLOSED"
    assert t.net_pnl is not None


def test_exit_fill_then_close_trade_override_applies(tmp_path):
    """TradeCloseManager calls record_fill(exit) then close_trade(gross/net/fees);
    the authoritative P&L override must still be stamped after the exit fill
    already closed & purged the trade from the open cache."""
    from execution.fee_model import MCXFeeModel
    tl = _ledger(tmp_path)
    tl.create_trade(strategy_id="silver_01", instrument="SILVERM", side="SHORT",
                    entry_quantity=1, signal_time=1000.0, trigger_price=236980.0,
                    stop_price=237200.0, multiplier=5.0, trade_id="ps", position_id="ps")
    tl.record_fill("ps", "pse", "pso", "SELL", 1, 236980.0, 1001.0, is_entry=True)
    tl.record_fill("ps", "psx", "psx", "BUY", 1, 236400.0, 2000.0, is_entry=False)
    # trade is now CLOSED and purged from the open cache
    assert len(tl.get_open_trades()) == 0

    fb = MCXFeeModel()
    gross = (236980.0 - 236400.0) * 1 * 5.0
    fbd = fb.calculate(236980.0, 236400.0, 1, 5.0, side="SHORT")
    net = gross - fbd.total
    tl.close_trade("ps", exit_reason="sl_exit", gross_pnl=gross, net_pnl=net, fees=fbd.total)
    t = tl.get_trade("ps")
    assert t.exit_reason == "sl_exit"
    assert t.net_pnl == pytest.approx(net)
    assert t.fees == pytest.approx(fbd.total)
    assert t.gross_pnl - t.fees == pytest.approx(t.net_pnl)
    # still absent from open cache after the override
    assert len(tl.get_open_trades()) == 0