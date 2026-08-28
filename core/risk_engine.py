"""Risk engine for portfolio-level risk management."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional


class RiskEngine:
    """Portfolio-level risk management engine.
    
    Enforces:
    - Per-strategy position limits
    - Per-instrument exposure limits
    - Total portfolio exposure limits
    - Margin requirements
    - Daily loss limits
    - Maximum drawdown
    - Kill switch
    """

    def __init__(
        self,
        max_positions_per_strategy: int = 1,
        max_positions_total: int = 8,
        max_daily_loss: float = 999_999_999.0,
        max_drawdown_pct: float = 100.0,
        kill_switch_enabled: bool = False,
    ):
        self.max_positions_per_strategy = max_positions_per_strategy
        self.max_positions_total = max_positions_total
        self.max_daily_loss = max_daily_loss
        self.max_drawdown_pct = max_drawdown_pct
        self.kill_switch_enabled = kill_switch_enabled
        self._lock = threading.RLock()

        self._kill_switch_active = False
        self._daily_pnl: float = 0.0
        self._peak_equity: float = 0.0
        self._last_reset_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check_order(
        self,
        signal: Any,
        current_positions: int,
        strategy_positions: int,
        available_margin: float,
        margin_required: float,
        current_equity: float,
    ) -> tuple[bool, Optional[str]]:
        """Check if order passes risk checks.
        
        Returns:
            (allowed, reason) - reason is None if allowed
        """
        with self._lock:
            # Kill switch check
            if self._kill_switch_active:
                return False, "kill_switch_active"

            # Max positions per strategy
            if strategy_positions >= self.max_positions_per_strategy:
                return False, "max_positions_per_strategy_reached"

            # Max total positions
            if current_positions >= self.max_positions_total:
                return False, "max_positions_total_reached"

            # Margin check
            if margin_required > available_margin:
                return False, "insufficient_margin"

            # Daily loss check
            if self._daily_pnl <= -self.max_daily_loss:
                self._activate_kill_switch()
                return False, "daily_loss_limit_reached"

            # Max drawdown check
            if self._peak_equity > 0:
                drawdown_pct = (self._peak_equity - current_equity) / self._peak_equity * 100
                if drawdown_pct >= self.max_drawdown_pct:
                    self._activate_kill_switch()
                    return False, "max_drawdown_reached"

            return True, None

    def update_daily_pnl(self, pnl: float) -> None:
        """Update running daily P&L. Auto-resets at start of new trading day."""
        with self._lock:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._last_reset_date:
                self._daily_pnl = 0.0
                self._peak_equity = 0.0
                self._last_reset_date = today
                print(f"[Risk] Daily reset for {today}", flush=True)
            self._daily_pnl += pnl

    def update_peak_equity(self, equity: float) -> None:
        """Update peak equity for drawdown calculation."""
        with self._lock:
            self._peak_equity = max(self._peak_equity, equity)

    def _activate_kill_switch(self) -> None:
        """Activate kill switch to stop all trading."""
        if self.kill_switch_enabled:
            with self._lock:
                self._kill_switch_active = True
            print("[RISK] KILL SWITCH ACTIVATED", flush=True)

    def deactivate_kill_switch(self) -> None:
        """Manually deactivate kill switch."""
        with self._lock:
            self._kill_switch_active = False

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    def reset_daily(self) -> None:
        """Reset daily P&L (call at start of new trading day)."""
        with self._lock:
            self._daily_pnl = 0.0

    def snapshot(self) -> dict:
        """Get risk engine state."""
        with self._lock:
            return {
                "kill_switch_active": self._kill_switch_active,
                "daily_pnl": self._daily_pnl,
                "peak_equity": self._peak_equity,
            }

    def restore(self, data: dict) -> None:
        """Restore risk engine state."""
        with self._lock:
            self._kill_switch_active = data.get("kill_switch_active", False)
            self._daily_pnl = data.get("daily_pnl", 0.0)
            self._peak_equity = data.get("peak_equity", 0.0)
