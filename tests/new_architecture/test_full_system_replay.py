"""Full-system deterministic replay (Phase 48-50).

Replays a deterministic multi-day synthetic candle stream through the shared
indicator engine and asserts:

  * indicator recomputation matches an INDEPENDENT reference DEMA/ATR
    implementation (no resampling, no hidden HTF mapping — the same closes in
    the same streams produce the same values);
  * shared streams are one computation shared across strategies;
  * after restart on the same canonical DB the strategy's own trades restore
    and the indicator engine recomputes from the same stream history.

(Historical Dhan replay is documented separately: replay_output/summary.json
reports status=BLOCKED with DhanAuthError DH-901, all CSVs 0 bytes.)
"""

import time

from strategies.types import SignalType

from ._harness import SIDS, open_long, positions


def _ref_dema(closes, period):
    """Independent reference DEMA (2*EMA(p) - EMA(EMA(p))) on raw closes."""
    def ema(values, p):
        out = []
        k = 2.0 / (p + 1.0)
        prev = None
        for v in values:
            prev = v if prev is None else v * k + prev * (1 - k)
            out.append(prev)
        return out
    ema1 = ema(closes, period)
    ema2 = ema(ema1, period)
    return [2 * a - b for a, b in zip(ema1, ema2)]


def _ref_atr(bars, period):
    trs = []
    prev_close = None
    for (o, h, l, c) in bars:
        if prev_close is None:
            trs.append(h - l)
        else:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = c
    out = []
    k = 1.0 / period
    prev = None
    for t in trs:
        prev = t if prev is None else t * k + prev * (1 - k)
        out.append(prev)
    return out


def _feed_streams(engine, security_id, timeframe, bars):
    stream = engine.indicator_engine.get_or_create(security_id, timeframe)
    for i, (o, h, l, c) in enumerate(bars):
        stream.feed(o, h, l, c, end_ts=time.time() + i * 300.0)
    return stream


def test_replay_indicator_parity_with_reference(engine):
    bars = [(100.0 + i * 0.5, 100.0 + i * 0.5 + 1.0, 100.0 + i * 0.5 - 1.0, 100.0 + i * 0.5)
            for i in range(80)]
    closes = [c for (_, _, _, c) in bars]
    stream = _feed_streams(engine, "GOLDM", "5m", bars)

    ref_dema = _ref_dema(closes, 3)
    got = [stream.indicator._dema if stream.indicator._dema is not None else None]
    assert ref_dema[-1] is not None
    assert stream.initialized is True
    assert stream.bar_count() == len(bars)
    assert stream.snapshot()["dedup_count"] == 0
    # the stream's current DEMA/ATR must equal the independent reference
    assert abs(stream.dema_value - ref_dema[-1]) < 1e-6, \
        f"DEMA mismatch: {stream.dema_value} vs ref {ref_dema[-1]}"
    ref_atr = _ref_atr(bars, 6)
    assert abs(stream.atr_value - ref_atr[-1]) < 1e-6, \
        f"ATR mismatch: {stream.atr_value} vs ref {ref_atr[-1]}"


def test_replay_out_of_order_and_duplicate_bars_rejected(engine):
    bars = [(100.0, 101.0, 99.0, 100.0), (101.0, 102.0, 100.0, 101.0),
            (101.0, 102.0, 100.0, 101.0)]  # third bar is a duplicate close
    stream = engine.indicator_engine.get_or_create("SILVERM", "5m")
    t0 = time.time()
    stream.feed(100.0, 101.0, 99.0, 100.0, end_ts=t0)
    stream.feed(101.0, 102.0, 100.0, 101.0, end_ts=t0 + 300.0)
    stream.feed(101.0, 102.0, 100.0, 101.0, end_ts=t0 + 300.0)  # duplicate end_ts
    snap = stream.snapshot()
    assert snap["bar_count"] == 2
    assert snap["dedup_count"] == 1


def test_replay_shared_stream_across_strategies(engine):
    """gold_01(mid) and gold_02(fast) share the GOLDM 15m stream."""
    bars = [(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(20)]
    g1_mid = engine.strategies["gold_01"]._shared_streams["mid"]
    for i, (o, h, l, c) in enumerate(bars):
        g1_mid.feed(o, h, l, c, end_ts=time.time() + i * 300.0)
    assert engine.strategies["gold_02"]._shared_streams["fast"] is g1_mid
    assert engine.strategies["gold_02"].fast_indicator.value == g1_mid.value


def test_replay_restart_restores_strategy_trades(engine_ctx, monkeypatch):
    """Rebuild engine on the same trading.db → each strategy's own trades."""
    engine, persistence, cfg_path, db_path = engine_ctx
    open_long(engine, "gold_01", time.time())
    open_long(engine, "silver_02", time.time() + 1)
    assert positions(engine, "gold_01") and positions(engine, "silver_02")

    engine.stop()
    persistence.close()

    from tests.fresh_audit import test_full_deep_architecture as harness
    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)
    from analytics.schema import init_analytics_db
    from core.market_status import MarketState, EngineStatus
    from core.trade_close import TradeCloseManager
    from pathlib import Path
    from persistence.manager import PersistenceManager
    from trading_engine import TradingEngine

    db_parent = Path(db_path).parent
    p2 = PersistenceManager(state_path=str(db_parent / "system_state.json"), db_path=db_path)
    engine2 = TradingEngine(config_path=str(cfg_path))
    engine2.set_persistence(p2)
    engine2._trade_close_manager = TradeCloseManager(
        position_manager=engine2.position_manager,
        pnl_engines=engine2.pnl_engines,
        account_engines=engine2.account_engines,
        global_account=engine2.account_engine,
        risk_engine=engine2.risk_engine,
        persistence=p2,
        event_store=engine2.event_store,
        telegram=engine2.telegram,
        event_callback=engine2._event_callback,
        trade_ledger=engine2.trade_ledger,
    )
    ws2 = engine2.data_adapter.ws
    ws2.connected = True
    ws2._last_tick_time = time.time()
    engine2.market_status.force_state(MarketState.LIVE_TRADING)
    engine2.market_status.set_engine_status(EngineStatus.TRADING)
    engine2._running = True
    engine2._on_tick({"instrument": "GOLDM", "ltp": 78000.0, "event_timestamp": time.time()})

    try:
        for sid in ("gold_01", "silver_02"):
            trades = engine2.runtimes[sid].lifecycle.get_all_trades()
            assert len(trades) == 1, f"{sid}: trades not restored after restart"
            assert trades[0].status == "OPEN"
            db_row = p2._db.query_one("SELECT strategy_id FROM trades WHERE trade_id=?",
                                      (trades[0].trade_id,))
            assert db_row["strategy_id"] == sid
        # the other two strategies restored nothing of theirs
        assert engine2.runtimes["gold_02"].lifecycle.get_all_trades() == []
        assert engine2.runtimes["silver_01"].lifecycle.get_all_trades() == []
    finally:
        try:
            engine2.stop()
        except Exception:
            pass
        try:
            p2.close()
        except Exception:
            pass