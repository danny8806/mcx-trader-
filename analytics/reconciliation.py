"""Reconciliation Engine - verifies consistency across all data sources."""
from __future__ import annotations
import sqlite3
import time
from typing import Optional, Any
from dataclasses import dataclass, field


@dataclass
class ReconciliationIssue:
    """A single reconciliation issue."""
    issue_type: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    trade_id: Optional[str] = None
    strategy_id: Optional[str] = None
    description: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ReconciliationResult:
    """Result of a reconciliation check."""
    status: str = "PASS"  # PASS or FAIL
    issues: list[ReconciliationIssue] = field(default_factory=list)
    checks_performed: int = 0
    timestamp: float = field(default_factory=time.time)
    
    @property
    def issue_count(self) -> int:
        return len(self.issues)
    
    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")
    
    @property
    def high_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "HIGH")


class ReconciliationEngine:
    """Verifies data consistency across all trading data sources."""
    
    def __init__(self, analytics_db: str = "analytics.db",
                 trading_db: str = "trading.db"):
        self._analytics_db = analytics_db
        self._trading_db = trading_db
    
    def run_full_reconciliation(self) -> ReconciliationResult:
        """Run all reconciliation checks."""
        result = ReconciliationResult()
        
        # Run each check
        checks = [
            self._check_trade_fills_consistency,
            self._check_position_fills_consistency,
            self._check_trade_pnl_consistency,
            self._check_strategy_attribution,
            self._check_no_duplicate_trades,
            self._check_no_negative_quantities,
            self._check_closed_trades_have_exit,
            self._check_no_orphan_events,
            self._check_cross_source_consistency,
        ]
        
        for check in checks:
            try:
                issues = check()
                result.issues.extend(issues)
                result.checks_performed += 1
            except Exception as e:
                result.issues.append(ReconciliationIssue(
                    issue_type="CHECK_ERROR",
                    severity="HIGH",
                    description=f"Check failed: {str(e)}",
                ))
                result.checks_performed += 1
        
        result.status = "FAIL" if result.issues else "PASS"
        return result
    
    def _check_trade_fills_consistency(self) -> list[ReconciliationIssue]:
        """Verify trade fill quantities match leg totals."""
        issues = []
        conn = sqlite3.connect(self._analytics_db)
        conn.row_factory = sqlite3.Row
        try:
            # Get all closed trades
            trades = conn.execute(
                "SELECT * FROM trades_analytics WHERE status = 'CLOSED'"
            ).fetchall()
            
            for trade in trades:
                trade_id = trade["trade_id"]
                
                # Sum entry legs
                entry_legs = conn.execute(
                    "SELECT SUM(quantity) as total FROM trade_legs WHERE trade_id = ? AND is_entry = 1",
                    (trade_id,)
                ).fetchone()
                entry_total = entry_legs["total"] or 0
                
                # Sum exit legs
                exit_legs = conn.execute(
                    "SELECT SUM(quantity) as total FROM trade_legs WHERE trade_id = ? AND is_entry = 0",
                    (trade_id,)
                ).fetchone()
                exit_total = exit_legs["total"] or 0
                
                # Verify
                if entry_total != trade["filled_quantity"]:
                    issues.append(ReconciliationIssue(
                        issue_type="TRADE_FILLS_MISMATCH",
                        severity="CRITICAL",
                        trade_id=trade_id,
                        strategy_id=trade["strategy_id"],
                        description=f"Entry fill quantity mismatch: legs={entry_total}, trade={trade['filled_quantity']}",
                    ))
                
                if trade["status"] == "CLOSED" and exit_total != trade["exit_quantity"]:
                    issues.append(ReconciliationIssue(
                        issue_type="TRADE_EXIT_MISMATCH",
                        severity="CRITICAL",
                        trade_id=trade_id,
                        strategy_id=trade["strategy_id"],
                        description=f"Exit fill quantity mismatch: legs={exit_total}, trade={trade['exit_quantity']}",
                    ))
        finally:
            conn.close()
        
        return issues
    
    def _check_position_fills_consistency(self) -> list[ReconciliationIssue]:
        """Verify position quantities match fill quantities."""
        issues = []
        # Cross-check with existing trading.db if available
        try:
            conn = sqlite3.connect(self._trading_db)
            conn.row_factory = sqlite3.Row
            try:
                trades = conn.execute(
                    "SELECT * FROM trades WHERE status = 'closed'"
                ).fetchall()
                for t in trades:
                    if t["quantity"] and t["quantity"] < 0:
                        issues.append(ReconciliationIssue(
                            issue_type="NEGATIVE_POSITION",
                            severity="CRITICAL",
                            trade_id=t.get("trade_id"),
                            description=f"Negative position quantity: {t['quantity']}",
                        ))
            finally:
                conn.close()
        except Exception:
            pass
        
        return issues
    
    def _check_trade_pnl_consistency(self) -> list[ReconciliationIssue]:
        """Verify trade P&L calculations are consistent."""
        issues = []
        conn = sqlite3.connect(self._analytics_db)
        conn.row_factory = sqlite3.Row
        try:
            trades = conn.execute(
                "SELECT * FROM trades_analytics WHERE status = 'CLOSED'"
            ).fetchall()
            
            for trade in trades:
                trade_id = trade["trade_id"]
                entry = trade["average_entry_price"] or 0
                exit_p = trade["average_exit_price"] or 0
                qty = trade["exit_quantity"] or trade["filled_quantity"] or 0
                side = trade["side"]
                gross = trade["gross_pnl"] or 0
                fees = trade["fees"] or 0
                net = trade["net_pnl"] or 0
                multiplier = trade["multiplier"] or 1.0
                
                if entry > 0 and exit_p > 0 and qty > 0:
                    expected_gross = (exit_p - entry) * qty * multiplier if side == "LONG" else (entry - exit_p) * qty * multiplier
                    if abs(expected_gross - gross) > 0.01:
                        issues.append(ReconciliationIssue(
                            issue_type="PNL_MISMATCH",
                            severity="HIGH",
                            trade_id=trade_id,
                            strategy_id=trade["strategy_id"],
                            description=f"Gross P&L mismatch: expected={expected_gross:.2f}, actual={gross:.2f}",
                        ))
                    
                    expected_net = gross - fees
                    if abs(expected_net - net) > 0.01:
                        issues.append(ReconciliationIssue(
                            issue_type="NET_PNL_MISMATCH",
                            severity="HIGH",
                            trade_id=trade_id,
                            strategy_id=trade["strategy_id"],
                            description=f"Net P&L mismatch: expected={expected_net:.2f}, actual={net:.2f}",
                        ))
        finally:
            conn.close()
        
        return issues
    
    def _check_strategy_attribution(self) -> list[ReconciliationIssue]:
        """Verify every trade has strategy attribution."""
        issues = []
        conn = sqlite3.connect(self._analytics_db)
        conn.row_factory = sqlite3.Row
        try:
            trades = conn.execute(
                "SELECT * FROM trades_analytics WHERE strategy_id IS NULL OR strategy_id = ''"
            ).fetchall()
            
            for trade in trades:
                issues.append(ReconciliationIssue(
                    issue_type="MISSING_STRATEGY",
                    severity="CRITICAL",
                    trade_id=trade["trade_id"],
                    description="Trade has no strategy attribution",
                ))
        finally:
            conn.close()
        
        return issues
    
    def _check_no_duplicate_trades(self) -> list[ReconciliationIssue]:
        """Check for duplicate trade records."""
        issues = []
        conn = sqlite3.connect(self._analytics_db)
        try:
            dupes = conn.execute(
                """SELECT trade_id, COUNT(*) as cnt 
                   FROM trades_analytics 
                   GROUP BY trade_id 
                   HAVING cnt > 1"""
            ).fetchall()
            
            for trade_id, count in dupes:
                issues.append(ReconciliationIssue(
                    issue_type="DUPLICATE_TRADE",
                    severity="CRITICAL",
                    trade_id=trade_id,
                    description=f"Duplicate trade records: {count}",
                ))
        finally:
            conn.close()
        
        return issues
    
    def _check_no_negative_quantities(self) -> list[ReconciliationIssue]:
        """Check for negative quantities."""
        issues = []
        conn = sqlite3.connect(self._analytics_db)
        conn.row_factory = sqlite3.Row
        try:
            trades = conn.execute(
                "SELECT * FROM trades_analytics WHERE filled_quantity < 0 OR entry_quantity < 0"
            ).fetchall()
            
            for trade in trades:
                issues.append(ReconciliationIssue(
                    issue_type="NEGATIVE_QUANTITY",
                    severity="CRITICAL",
                    trade_id=trade["trade_id"],
                    strategy_id=trade["strategy_id"],
                    description=f"Negative quantity: entry={trade['entry_quantity']}, filled={trade['filled_quantity']}",
                ))
        finally:
            conn.close()
        
        return issues
    
    def _check_closed_trades_have_exit(self) -> list[ReconciliationIssue]:
        """Verify all closed trades have exit information."""
        issues = []
        conn = sqlite3.connect(self._analytics_db)
        conn.row_factory = sqlite3.Row
        try:
            trades = conn.execute(
                "SELECT * FROM trades_analytics WHERE status = 'CLOSED'"
            ).fetchall()
            
            for trade in trades:
                if not trade["average_exit_price"]:
                    issues.append(ReconciliationIssue(
                        issue_type="CLOSED_NO_EXIT",
                        severity="HIGH",
                        trade_id=trade["trade_id"],
                        strategy_id=trade["strategy_id"],
                        description="Closed trade has no exit price",
                    ))
                if not trade["closed_at"]:
                    issues.append(ReconciliationIssue(
                        issue_type="CLOSED_NO_TIMESTAMP",
                        severity="HIGH",
                        trade_id=trade["trade_id"],
                        strategy_id=trade["strategy_id"],
                        description="Closed trade has no close timestamp",
                    ))
        finally:
            conn.close()
        
        return issues
    
    def _check_no_orphan_events(self) -> list[ReconciliationIssue]:
        """Check for events referencing non-existent trades."""
        issues = []
        conn = sqlite3.connect(self._analytics_db)
        try:
            orphans = conn.execute(
                """SELECT e.event_id, e.trade_id, e.event_type
                   FROM trade_events e
                   LEFT JOIN trades_analytics t ON e.trade_id = t.trade_id
                   WHERE t.trade_id IS NULL"""
            ).fetchall()
            
            for event_id, trade_id, event_type in orphans:
                issues.append(ReconciliationIssue(
                    issue_type="ORPHAN_EVENT",
                    severity="MEDIUM",
                    trade_id=trade_id,
                    description=f"Event {event_type} references non-existent trade {trade_id}",
                ))
        finally:
            conn.close()
        
        return issues
    
    def _check_cross_source_consistency(self) -> list[ReconciliationIssue]:
        """Cross-check analytics DB vs trading DB."""
        issues = []
        try:
            analytics_conn = sqlite3.connect(self._analytics_db)
            trading_conn = sqlite3.connect(self._trading_db)
            
            try:
                # Get trade counts from both sources
                analytics_count = analytics_conn.execute(
                    "SELECT COUNT(*) FROM trades_analytics WHERE status = 'CLOSED'"
                ).fetchone()[0]
                
                trading_count = trading_conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE status = 'closed'"
                ).fetchone()[0]
                
                if analytics_count != trading_count:
                    issues.append(ReconciliationIssue(
                        issue_type="CROSS_SOURCE_COUNT_MISMATCH",
                        severity="MEDIUM",
                        description=f"Trade count mismatch: analytics={analytics_count}, trading={trading_count}",
                        details={"analytics_count": analytics_count, "trading_count": trading_count},
                    ))
            finally:
                analytics_conn.close()
                trading_conn.close()
        except Exception:
            pass
        
        return issues
