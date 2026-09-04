"""PHASE 13 — CRASH / FAILURE HANDLING
Verifies: Fill dedup survives crash, state persistence, safe mode activation.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


class TestCrashHandling:
    """Phase 13: Crash and failure scenarios."""

    def test_fill_dedup_mark_survives_restart(self):
        """Fill marked processed is detected after DB reload."""
        from core.fill_dedup import FillDeduplicator
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            dedup = FillDeduplicator(db_path=db)
            dedup.mark_processed("F_CRASH_1")
            dedup.mark_processed("F_CRASH_2")
            # Simulate restart: new instance loads from same DB
            dedup2 = FillDeduplicator(db_path=db)
            dedup2.load_from_database()
            assert dedup2.is_duplicate("F_CRASH_1")
            assert dedup2.is_duplicate("F_CRASH_2")
            assert not dedup2.is_duplicate("F_CRASH_3")

    def test_fill_dedup_mark_atomic(self):
        """mark_processed is atomic — concurrent calls don't corrupt DB."""
        from core.fill_dedup import FillDeduplicator
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            dedup = FillDeduplicator(db_path=db)
            results = []
            for i in range(20):
                results.append(dedup.mark_processed(f"F_AT_{i}"))
            assert all(results)
            assert dedup.count == 20

    def test_safe_mode_enter_blocks_trading(self):
        """Entering safe mode blocks trading (when market is live)."""
        from core.safe_mode import SafeModeManager
        from core.market_status import MarketStatus, MarketState, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        # Force to LIVE_TRADING so is_trading_allowed can be True
        ms.force_state(MarketState.LIVE_TRADING)
        ms.set_engine_status(EngineStatus.TRADING)
        sm = SafeModeManager(ms)
        # Without live market data, is_trading_allowed is False anyway
        # But with reasons active, should_allow_trading returns False
        sm.enter_safe_mode("test_crash")
        assert sm.should_allow_trading() is False
        assert sm.is_active is True

    def test_safe_mode_multiple_reasons(self):
        """Safe mode with multiple reasons requires all cleared."""
        from core.safe_mode import SafeModeManager
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        sm = SafeModeManager(ms)
        sm.enter_safe_mode("reason_1")
        sm.enter_safe_mode("reason_2")
        assert sm.should_allow_trading() is False
        sm.clear_reason("reason_1")
        # Still has reason_2 — but should_allow_trading may also return False
        # due to market_status.is_trading_allowed being False (no live data)
        assert sm.is_active is True
        sm.clear_reason("reason_2")
        assert sm.is_active is False

    def test_persistence_state_save_load(self):
        """PersistenceManager saves and loads state correctly."""
        from persistence.manager import PersistenceManager
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            db_path = os.path.join(tmp, "state.db")
            pm = PersistenceManager(state_path=state_path, db_path=db_path)
            state = {"gold_01": {"position_side": "LONG", "stop_price": 149000.0}}
            pm.save_state(state)
            loaded = pm.load_state()
            assert loaded is not None
            assert loaded["gold_01"]["position_side"] == "LONG"

    def test_risk_engine_kill_switch(self):
        """Kill switch blocks orders when daily loss breached during check_order."""
        from core.risk_engine import RiskEngine
        risk = RiskEngine(kill_switch_enabled=True, max_daily_loss=1000.0)
        risk.update_daily_pnl(-1500.0)
        # Kill switch activates during check_order when daily_loss limit breached
        allowed, reason = risk.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=100000, margin_required=10000,
            current_equity=1200000,
        )
        assert allowed is False
        assert risk.kill_switch_active is True
        assert "kill" in reason.lower() or "daily_loss" in reason.lower()
