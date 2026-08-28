"""Reconciliation routes."""
from __future__ import annotations
import asyncio
import time
from fastapi import APIRouter
router = APIRouter()
_engine = None

def init(engine, event_bus):
    global _engine
    _engine = engine

def _run_reconciliation_sync():
    if not _engine:
        return {"error": "Engine not initialized"}
    if getattr(_engine, "_persistence", None) is None:
        return {"error": "Persistence not initialized"}
    try:
        from reconciliation.engine import ReconciliationEngine
        recon = ReconciliationEngine(
            persistence=_engine._persistence,
            position_manager=_engine.position_manager,
            pnl_engines=_engine.pnl_engines,
            account_engines=_engine.account_engines,
            strategies=_engine.strategies,
            order_manager=_engine.order_manager,
        )
        result = recon.reconcile(phase="live")
        return {
            "is_consistent": result.is_consistent,
            "phase": result.phase,
            "timestamp": result.timestamp,
            "errors": result.errors,
            "warnings": result.warnings,
            "stats": result.stats,
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/reconciliation")
async def get_reconciliation():
    if not _engine:
        return {"error": "Engine not initialized"}
    return await asyncio.to_thread(_run_reconciliation_sync)