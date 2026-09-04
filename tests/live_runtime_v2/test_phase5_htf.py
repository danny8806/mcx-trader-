"""
PHASE 5 — HTF MAPPING / LOOKAHEAD TEST
=======================================
Verify exact mapping: 5m -> 15m, 5m -> 1h
A fast bar MUST NEVER consume a future/unclosed HTF bar.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence, REPORT_DIR


@dataclass
class TestBar:
    instrument: str
    timeframe: str
    start_ts: float
    end_ts: float
    open: float
    high: float
    low: float
    close: float
    volume: int = 100


class TestHTFMappingLookahead:
    """Phase 5: Verify HTF mapping uses no future data."""

    def test_exact_htf_boundary_no_lookahead(self):
        """At exact HTF boundary, mapping returns the COMPLETED HTF bar, not the current."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        from indicators.dema_atr import DEMAATR

        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6, atr_factor=1.0)

        # Feed 1h bars
        for i in range(5):
            bar = TestBar("GOLDM", "1h",
                          start_ts=1000 + i * 3600,
                          end_ts=1000 + (i + 1) * 3600,
                          open=100 + i, high=102 + i, low=98 + i, close=101 + i)
            htf.on_htf_bar_closed(bar)

        # Fast bar ends at exactly 1h bar end_ts
        fast_bar = TestBar("GOLDM", "5m",
                           start_ts=1000 + 3600 - 300,
                           end_ts=1000 + 3600,
                           open=105, high=107, low=103, close=106)
        result = htf.map_to_fast_bar(fast_bar, "5m")

        assert result.htf_value is not None, "HTF value should not be None at boundary"
        # Should map to the bar that ended at or before 1000+3600
        assert result.htf_source_timestamp <= fast_bar.end_ts, \
            f"HTF source timestamp {result.htf_source_timestamp} > fast bar end {fast_bar.end_ts}"

    def test_one_second_before_boundary(self):
        """One second before HTF boundary maps to previous HTF bar."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6, atr_factor=1.0)

        for i in range(5):
            bar = TestBar("GOLDM", "1h",
                          start_ts=1000 + i * 3600,
                          end_ts=1000 + (i + 1) * 3600,
                          open=100 + i, high=102 + i, low=98 + i, close=101 + i)
            htf.on_htf_bar_closed(bar)

        # Fast bar ends 1 second before 1h bar boundary
        fast_bar = TestBar("GOLDM", "5m",
                           start_ts=1000 + 3600 - 301,
                           end_ts=1000 + 3600 - 1,
                           open=105, high=107, low=103, close=106)
        result = htf.map_to_fast_bar(fast_bar, "5m")

        if result.htf_value is not None:
            assert result.htf_source_timestamp < fast_bar.end_ts

    def test_no_future_htf_bar_consumed(self):
        """Fast bar never sees HTF bar that hasn't closed yet."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6, atr_factor=1.0)

        # Only feed bars up to time 1000+4*3600
        for i in range(4):
            bar = TestBar("GOLDM", "1h",
                          start_ts=1000 + i * 3600,
                          end_ts=1000 + (i + 1) * 3600,
                          open=100 + i, high=102 + i, low=98 + i, close=101 + i)
            htf.on_htf_bar_closed(bar)

        # Fast bar within the 4th hour (no 5th hour bar exists)
        fast_bar = TestBar("GOLDM", "5m",
                           start_ts=1000 + 3 * 3600 + 2700,
                           end_ts=1000 + 3 * 3600 + 3000,
                           open=105, high=107, low=103, close=106)
        result = htf.map_to_fast_bar(fast_bar, "5m")

        if result.htf_source_timestamp is not None:
            assert result.htf_source_timestamp <= 1000 + 4 * 3600, \
                f"Source timestamp {result.htf_source_timestamp} references future HTF bar"

    def test_mapping_uses_bisect_right(self):
        """Verify the mapping uses bisect_right - 1 (same as backtest)."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6, atr_factor=1.0)

        end_times = []
        for i in range(10):
            bar = TestBar("GOLDM", "1h",
                          start_ts=1000 + i * 3600,
                          end_ts=1000 + (i + 1) * 3600,
                          open=100 + i, high=102 + i, low=98 + i, close=101 + i)
            htf.on_htf_bar_closed(bar)
            end_times.append(1000 + (i + 1) * 3600)

        fast_bar = TestBar("GOLDM", "5m",
                           start_ts=1000 + 5 * 3600,
                           end_ts=1000 + 5 * 3600 + 300,
                           open=105, high=107, low=103, close=106)
        result = htf.map_to_fast_bar(fast_bar, "5m")

        # Independent bisect_right check
        independent_idx = bisect.bisect_right(end_times, fast_bar.end_ts) - 1
        runtime_ts = result.htf_source_timestamp
        if runtime_ts is not None:
            expected_ts = end_times[independent_idx] if 0 <= independent_idx < len(end_times) else None
            assert runtime_ts == expected_ts, \
                f"Runtime timestamp {runtime_ts} != expected {expected_ts}"

    def test_mid_mapping_15m(self):
        """15m HTF mapping also uses no lookahead."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "15m", dema_period=3, atr_period=6, atr_factor=1.0)

        for i in range(10):
            bar = TestBar("GOLDM", "15m",
                          start_ts=1000 + i * 900,
                          end_ts=1000 + (i + 1) * 900,
                          open=100 + i, high=102 + i, low=98 + i, close=101 + i)
            htf.on_htf_bar_closed(bar)

        fast_bar = TestBar("GOLDM", "5m",
                           start_ts=1000 + 3 * 900,
                           end_ts=1000 + 3 * 900 + 300,
                           open=105, high=107, low=103, close=106)
        result = htf.map_mid_to_fast_bar(fast_bar, "5m")
        # Should not reference timestamps after fast bar end
        if result.htf_source_timestamp is not None:
            assert result.htf_source_timestamp <= fast_bar.end_ts

    def test_session_boundary_mapping(self):
        """Session boundary: bars from previous session still map correctly."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6, atr_factor=1.0)

        # Session 1: three 1h bars
        for i in range(3):
            bar = TestBar("GOLDM", "1h",
                          start_ts=1000 + i * 3600,
                          end_ts=1000 + (i + 1) * 3600,
                          open=100 + i, high=102 + i, low=98 + i, close=101 + i)
            htf.on_htf_bar_closed(bar)

        # Fast bar in session 2 (after gap)
        fast_bar = TestBar("GOLDM", "5m",
                           start_ts=1000 + 3 * 3600 + 1000,
                           end_ts=1000 + 3 * 3600 + 1300,
                           open=105, high=107, low=103, close=106)
        result = htf.map_to_fast_bar(fast_bar, "5m")
        if result.htf_value is not None:
            assert result.htf_source_timestamp <= fast_bar.end_ts
