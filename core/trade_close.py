"""Atomic trade close with guaranteed consistency.

Order of operations:
1. Calculate P&L (pure calculation, no side effects)
2. Persist trade to database FIRST (before memory update)
3. Persist fill to database
4. Close position in memory
5. Update account P&L
6. Update risk engine
7. Record event
8. Send Telegram (notification only, after persistence)

If step 2-3 fails: return False, do NOT update memory.
If step 4-8 fails: state is recoverable from database on restart.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from execution.paper_broker import Fill


class TradeCloseManager:
    """Atomic trade close with guaranteed consistency."""

    def __init__(
        self,
        position_manager,
        pnl_engines: dict[str, Any],
        account_engines: dict[str, Any],
        global_account: Any,
        risk_engine,
        persistence,
        event_store,
        telegram=None,
        event_callback=None,
        trade_ledger=None,
    ):
        self._position_manager = position_manager
        self._pnl_engines = pnl_engines
        self._account_engines = account_engines
        self._global_account = global_account
        self._risk_engine = risk_engine
        self._persistence = persistence
        self._event_store = event_store
        self._telegram = telegram
        self._event_callback = event_callback
        self._trade_ledger = trade_ledger

    def close_position(self, fill: Fill, position, strategy_id: str, multiplier: float) -> bool:
        """Atomically close a position.

        Args:
            fill: The exit fill.
            position: The open Position object.
            strategy_id: Strategy identifier.
            multiplier: Contract multiplier.

        Returns:
            True if close completed successfully, False if persistence failed.
        """
        # ── Step 1: Calculate P&L (pure calculation, no side effects) ──
        pnl_engine = self._pnl_engines.get(strategy_id)
        if pnl_engine:
            entry_fill = Fill(
                fill_id=position.entry_fill_ids[0] if position.entry_fill_ids else "",
                order_id="",
                instrument=position.instrument,
                side="BUY" if position.is_long else "SELL",
                quantity=position.quantity,
                price=position.average_entry,
                timestamp=position.entry_timestamp,
                strategy_id=position.strategy_id,
                multiplier=multiplier,
            )
            gross_pnl, charges, net_pnl = pnl_engine.calculate_realized_pnl(
                entry_fill=entry_fill,
                exit_fill=fill,
                multiplier=multiplier,
            )
        else:
            gross_pnl, charges, net_pnl = 0.0, 0.0, 0.0

        side = "LONG" if position.is_long else "SHORT"
        entry_ts = (
            datetime.fromtimestamp(position.entry_timestamp, tz=timezone.utc).isoformat()
            if position.entry_timestamp else None
        )
        exit_ts = (
            datetime.fromtimestamp(fill.timestamp, tz=timezone.utc).isoformat()
            if fill.timestamp else None
        )
        exit_reason = position.exit_reason or "signal_exit"

        # ── Step 2: Persist trade to database FIRST ──
        if self._persistence:
            try:
                self._persistence.save_trade({
                    "trade_id": position.position_id,
                    "strategy_id": strategy_id,
                    "instrument": fill.instrument,
                    "side": side,
                    "entry_timestamp": entry_ts,
                    "entry_price": position.average_entry,
                    "exit_timestamp": exit_ts,
                    "exit_price": fill.price,
                    "quantity": position.quantity,
                    "multiplier": multiplier,
                    "gross_pnl": gross_pnl,
                    "charges": charges,
                    "net_pnl": net_pnl,
                    "exit_reason": exit_reason,
                    "status": "closed",
                })
            except Exception as e:
                print(f"[TradeClose] CRITICAL: Failed to persist trade: {e}", flush=True)
                return False

        # ── Step 3: Persist fill to database ──
        if self._persistence:
            try:
                self._persistence.save_fill({
                    "fill_id": fill.fill_id,
                    "order_id": fill.order_id,
                    "strategy_id": fill.strategy_id,
                    "instrument": fill.instrument,
                    "side": fill.side,
                    "quantity": fill.quantity,
                    "price": fill.price,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                print(f"[TradeClose] WARNING: Failed to persist exit fill (trade already saved): {e}", flush=True)
                # Continue — trade record is saved, fill is recoverable from reconciliation

        # ── Steps 4-8: Update in-memory state (recoverable from DB on restart) ──

        # Step 4: Close position in memory
        try:
            self._position_manager.close_position(
                position_id=position.position_id,
                fill=fill,
                reason=exit_reason,
            )
        except Exception as e:
            print(f"[TradeClose] WARNING: close_position memory update failed: {e}", flush=True)

        # Step 5: Update account P&L
        strat_account = self._account_engines.get(strategy_id)
        try:
            if strat_account:
                strat_account.update_realized_pnl(net_pnl, charges)
                strat_account.release_margin(position.margin)
            if self._global_account:
                self._global_account.update_realized_pnl(net_pnl, charges)
                self._global_account.release_margin(position.margin)
        except Exception as e:
            print(f"[TradeClose] WARNING: account update failed: {e}", flush=True)

        # Step 6: Update risk engine
        try:
            if self._risk_engine:
                self._risk_engine.update_daily_pnl(net_pnl)
                # Update peak equity for drawdown tracking after trade close
                if self._global_account and hasattr(self._global_account, 'equity'):
                    self._risk_engine.update_peak_equity(self._global_account.equity)
        except Exception as e:
            print(f"[TradeClose] WARNING: risk engine update failed: {e}", flush=True)

        # Step 6b: Close trade in ledger
        if self._trade_ledger:
            try:
                # Find the open trade for this position
                open_trades = self._trade_ledger.get_open_trades(
                    strategy_id=strategy_id, instrument=fill.instrument,
                )
                for t in open_trades:
                    self._trade_ledger.close_trade(
                        t.trade_id, exit_reason=exit_reason,
                        gross_pnl=gross_pnl, net_pnl=net_pnl, fees=charges,
                    )
            except Exception:
                pass

        # Step 7: Record event
        if self._event_store:
            try:
                self._event_store.record(
                    trade_id=position.position_id,
                    strategy_id=strategy_id,
                    instrument=fill.instrument,
                    event_type="TRADE_CLOSED",
                    payload={
                        "gross_pnl": gross_pnl,
                        "charges": charges,
                        "net_pnl": net_pnl,
                        "exit_reason": exit_reason,
                        "entry_price": position.average_entry,
                        "exit_price": fill.price,
                    },
                )
            except Exception:
                pass

        # Step 7b: Publish to dashboard EventBus + persistence
        if self._event_callback:
            try:
                self._event_callback("trade_closed", {
                    "trade_id": position.position_id,
                    "strategy_id": strategy_id,
                    "instrument": fill.instrument,
                    "side": side,
                    "entry_price": position.average_entry,
                    "exit_price": fill.price,
                    "quantity": position.quantity,
                    "gross_pnl": gross_pnl,
                    "charges": charges,
                    "net_pnl": net_pnl,
                    "exit_reason": exit_reason,
                })
            except Exception:
                pass
        if self._persistence:
            try:
                self._persistence.save_event({
                    "event_type": "trade_closed",
                    "strategy_id": strategy_id,
                    "instrument": fill.instrument,
                    "details": {
                        "trade_id": position.position_id,
                        "side": side,
                        "net_pnl": net_pnl,
                    },
                })
            except Exception:
                pass

        # Step 8: Send Telegram notification
        if self._telegram:
            try:
                entry_ts = position.entry_timestamp if hasattr(position, 'entry_timestamp') else 0
                duration_s = fill.timestamp - entry_ts if entry_ts else 0
                hrs, rem = divmod(int(duration_s), 3600)
                mins = rem // 60
                duration_str = f"{hrs}h {mins}m" if hrs else f"{mins}m"
                self._telegram.on_trade_close({
                    "instrument": fill.instrument,
                    "strategy_id": strategy_id,
                    "side": side,
                    "entry_price": position.average_entry,
                    "exit_price": fill.price,
                    "net_pnl": net_pnl,
                    "exit_reason": exit_reason,
                    "duration": duration_str,
                })
            except Exception:
                pass

        print(f"[TradeClose] Closed: strategy={strategy_id} P&L={net_pnl:.2f}", flush=True)
        return True
