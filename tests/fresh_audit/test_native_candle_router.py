"""MISSION §7 — NativeCandleRouter: single choke point for native candles.

Acceptance:
  §7  Candle identity = (security_id, timeframe, candle_end_ts).
  §7  Duplicates (same identity) are deduced and published once.
  §7  Out-of-order candles are detected and never published after a newer bar.
  §7  Incomplete candles are never published as completed candles.
  §7  Every closed candle is forwarded to the distributor exactly once.

Parity: the engine wires CandleFetcher -> candle_router -> distributor, and
the router's stats are exposed through engine.snapshot()["candle_router"].
"""
import pytest

from core.timeframe_engine import Bar
from data.native_router import NativeCandleRouter

INSTRUMENTS = {
    "GOLDM": {"security_id": "569003"},
    "SILVERM": {"security_id": "483080"},
}


def _bar(security, timeframe, end_ts, tf_min=5, start_ts=None):
    if start_ts is None:
        start_ts = end_ts - tf_min * 60
    return Bar(
        instrument=security, timeframe=timeframe,
        start_ts=start_ts, end_ts=end_ts,
        open=100.0, high=101.0, low=99.0, close=100.5, volume=1000,
    )


class _Collector:
    def __init__(self):
        self.bars = []

    def __call__(self, bar):
        self.bars.append(bar)


def _router(collector, instruments=INSTRUMENTS):
    return NativeCandleRouter(distributor=collector, instruments=instruments)


# ── identity ──────────────────────────────────────────────────────────────

def test_security_id_for_resolves_canonical_id():
    r = _router(_Collector())
    assert r.security_id_for("GOLDM") == "569003"
    assert r.security_id_for("SILVERM") == "483080"
    assert r.security_id_for("UNKNOWN") is None


def test_identity_uses_security_id_not_instrument_name():
    r = _router(_Collector())
    ident = r._identity(_bar("GOLDM", "5m", end_ts=100.0))
    assert ident == ("569003", "5m", 100.0)


def test_identity_falls_back_to_instrument_when_unmapped():
    r = _router(_Collector(), instruments={})
    ident = r._identity(_bar("GOLDM", "5m", end_ts=100.0))
    assert ident == ("GOLDM", "5m", 100.0)


# ── dedup ─────────────────────────────────────────────────────────────────

def test_duplicate_candle_deduplicated_published_once():
    collector = _Collector()
    r = _router(collector)
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=100.0)) is True
    # Same identity (security_id=569003, 5m, candle_end_ts=100) again.
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=100.0)) is False
    stats = r.stats()
    assert stats["published"] == 1
    assert stats["deduplicated"] == 1
    assert len(collector.bars) == 1


def test_duplicate_via_alias_instrument_deduplicated():
    """Two instrument tags resolving to the same security_id must dedupe."""
    collector = _Collector()
    r = _router(collector)
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=100.0)) is True
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=100.0)) is False
    assert len(collector.bars) == 1


# ── out-of-order ──────────────────────────────────────────────────────────

def test_out_of_order_detected_and_dropped():
    collector = _Collector()
    r = _router(collector)
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=300.0)) is True
    # An older candle on the same stream after a newer one — dropped.
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=100.0)) is False
    stats = r.stats()
    assert stats["published"] == 1
    assert stats["out_of_order"] == 1
    assert len(collector.bars) == 1


def test_streams_guarded_independently():
    collector = _Collector()
    r = _router(collector)
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=300.0)) is True
    # Different security + timeframe stream — a lower end_ts is NOT out of
    # order relative to the GOLDM stream.
    assert r.on_candle(_bar("SILVERM", "5m", end_ts=100.0)) is True
    assert r.on_candle(_bar("GOLDM", "15m", end_ts=50.0)) is True
    stats = r.stats()
    assert stats["published"] == 3
    assert stats["out_of_order"] == 0


# ── incomplete ────────────────────────────────────────────────────────────

def test_incomplete_candle_never_published():
    collector = _Collector()
    r = _router(collector)
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=100.0), is_complete=False) is False
    stats = r.stats()
    assert stats["incomplete_rejected"] == 1
    assert stats["published"] == 0
    assert len(collector.bars) == 0
    # Once it is complete, the same candle is forwarded.
    assert r.on_candle(_bar("GOLDM", "5m", end_ts=100.0), is_complete=True) is True
    assert len(collector.bars) == 1


# ── forwarding / aliases / diagnostics ────────────────────────────────────

def test_on_candle_closed_alias_forwards():
    collector = _Collector()
    r = _router(collector)
    assert r.on_candle_closed(_bar("GOLDM", "5m", end_ts=100.0)) is True
    assert r.candle_count == 1
    assert len(collector.bars) == 1


def test_stats_and_reset():
    collector = _Collector()
    r = _router(collector)
    r.on_candle(_bar("GOLDM", "5m", end_ts=100.0))
    r.on_candle(_bar("GOLDM", "5m", end_ts=100.0))
    assert r.stats()["streams"] == 1
    r.reset()
    s = r.stats()
    assert s["published"] == 0 and s["deduplicated"] == 0
    assert s["out_of_order"] == 0 and s["incomplete_rejected"] == 0


def test_none_bar_rejected():
    collector = _Collector()
    r = _router(collector)
    assert r.on_candle(None) is False
    assert r.candle_count == 0


# ── engine integration ────────────────────────────────────────────────────

def test_engine_candle_router_wired_and_guards(tmp_path, monkeypatch):
    from tests.fresh_audit import test_full_deep_architecture as harness
    monkeypatch.setattr("trading_engine.DhanDataAdapter", harness.MockDhanAdapter)
    from tests.fresh_audit.test_audit_reversal_sl_all_strategies import _write_config
    from trading_engine import TradingEngine
    from config import Config
    original = dict(Config._config)
    try:
        cfg_path = _write_config(tmp_path)
        engine = TradingEngine(config_path=str(cfg_path))
        try:
            router = engine.candle_router
            assert router is not None
            assert router.security_id_for("GOLDM") == "569003"

            bar = _bar("GOLDM", "5m", end_ts=1_700_000_000, tf_min=5)
            assert router.on_candle_closed(bar) is True
            # Duplicate on the same identity — not republished.
            assert router.on_candle_closed(bar) is False
            # Out-of-order older bar — not published.
            older = _bar("GOLDM", "5m", end_ts=1_699_990_000, tf_min=5)
            assert router.on_candle_closed(older) is False
            # Incomplete — rejected.
            assert router.on_candle(bar, is_complete=False) is False

            stats = router.stats()
            assert stats["published"] == 1
            assert stats["deduplicated"] == 1
            assert stats["out_of_order"] == 1
            assert stats["incomplete_rejected"] == 1

            # The engine snapshot surfaces router diagnostics.
            snap = engine.snapshot()["candle_router"]
            assert snap["published"] == 1
        finally:
            try:
                engine.stop()
            except Exception:
                pass
    finally:
        Config._config = original