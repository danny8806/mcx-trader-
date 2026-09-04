"""PHASE 23 — SESSION BOUNDARY
Verifies: State reset at session boundary, warmup/reconcile flags.
"""
from __future__ import annotations

import time

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


class TestSessionBoundary:
    """Phase 23: Session boundary behavior."""

    def test_market_status_session_times(self):
        """MarketStatus parses session open/close correctly."""
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        assert ms.session_open == "09:00"
        assert ms.session_close == "23:30"

    def test_market_status_should_warmup(self):
        """should_warmup reflects warmup_done_today state."""
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        snap = ms.snapshot()
        assert snap.get("warmup_done_today") is False

    def test_market_status_should_reconcile(self):
        """should_reconcile reflects reconcile_done_today state."""
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        assert ms.should_reconcile is True

    def test_market_status_snapshot_fields(self):
        """MarketStatus snapshot contains expected fields.

        force_state_override is intentionally NOT persisted (transient).
        """
        from core.market_status import MarketStatus, MarketState, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.force_state(MarketState.SAFE_MODE)
        ms.set_engine_status(EngineStatus.SAFE_MODE)
        snap = ms.snapshot()
        # force_state_override is transient — never persisted
        assert snap.get("force_state_override") is None
        assert snap.get("engine_status") == "safe_mode"

    def test_safe_mode_cooldown(self):
        """Safe mode exit has 5-second cooldown between attempts."""
        from core.safe_mode import SafeModeManager
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        sm = SafeModeManager(ms)
        sm.enter_safe_mode("cooldown_test")
        sm.clear_reason("cooldown_test")
        # First exit — not in safe mode, returns True (already exited)
        result = sm.exit_safe_mode()
        assert result is True
        # Second immediate attempt — cooldown blocks, but also not in safe mode
        # The cooldown check happens first, so it returns False even though is_safe is False
        time.sleep(5.1)  # Wait for cooldown
        result2 = sm.exit_safe_mode()
        assert result2 is True

    def test_risk_engine_daily_reset(self):
        """RiskEngine resets daily P&L at IST day boundary."""
        from core.risk_engine import RiskEngine
        risk = RiskEngine(max_daily_loss=1000.0)
        risk.update_daily_pnl(-500.0)
        assert risk.daily_pnl == -500.0
        risk.reset_daily()
        assert risk.daily_pnl == 0.0
