"""Reconciliation routes."""
from __future__ import annotations
import time
from fastapi import APIRouter
router = APIRouter()
_engine = None

def init(engine, event_bus):
    global _engine
    _engine = engine

@router.get("/api/reconciliation")
async def get_reconciliation():
    if not _engine:
        return {"error": "Engine not initialized"}
    return {
        "market_data": {"status": "pending", "message": "No live reconciliation configured"},
        "execution": {"status": "pending", "message": "Paper mode - no broker reconciliation needed"},
        "position": {"status": "pending", "message": "Internal positions only"},
        "timestamp": time.time(),
    }
