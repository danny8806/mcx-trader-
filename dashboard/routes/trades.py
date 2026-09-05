"""Trades routes - tradebook, trade details.

Serves canonical lifecycle data from TradeLifecycleManager as the single
source of truth. Falls back to persistence DB when lifecycle is empty.
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional
from fastapi import APIRouter
router = APIRouter()
_engine = None
_bus = None
_persistence = None

def init(engine, event_bus, persistence=None):
    global _engine, _bus, _persistence
    _engine = engine
    _bus = event_bus
    _persistence = persistence

def _list_trades_sync(strategy: Optional[str] = None, instrument: Optional[str] = None):
    try:
        # Primary: canonical lifecycle (per-strategy runtimes)
        if _engine and getattr(_engine, "runtimes", None) and len(_engine.runtimes) > 0:
            trades = []
            for rt in _engine.runtimes.all():
                try:
                    trades.extend(rt.lifecycle.get_trades_for_api(
                        strategy_id=strategy, instrument=instrument))
                except Exception:
                    pass
            if trades:
                return {"trades": trades, "count": len(trades), "source": "lifecycle"}

        # Fallback: persistence DB
        if _persistence:
            trades = _persistence.get_trades(strategy_id=strategy)
            if instrument:
                trades = [t for t in trades if t.get("instrument") == instrument.upper()]
            return {"trades": trades, "count": len(trades), "source": "persistence"}

        if not _engine:
            return {"error": "Engine not initialized"}
        fills = _engine.execution_engine.get_fills()
        result = []
        for f in fills:
            result.append({
                "fill_id": f.fill_id,
                "order_id": f.order_id,
                "instrument": f.instrument,
                "side": f.side,
                "quantity": f.quantity,
                "price": f.price,
                "timestamp": f.timestamp,
                "strategy_id": f.strategy_id,
            })
        return {"trades": result, "count": len(result), "source": "fills"}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/trades")
async def list_trades(strategy: Optional[str] = None, instrument: Optional[str] = None):
    return await asyncio.to_thread(_list_trades_sync, strategy, instrument)

def _get_trade_sync(trade_id: str):
    try:
        # Primary: canonical lifecycle (read-only aggregate over runtimes)
        if _engine and hasattr(_engine, "get_trade"):
            trade = _engine.get_trade(trade_id)
            if trade:
                return trade.snapshot()

        # Fallback: persistence DB
        if _persistence:
            trades = _persistence.get_trades()
            for t in trades:
                if t.get("trade_id") == trade_id:
                    return t
        return {"error": f"Trade {trade_id} not found"}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/trades/{trade_id}")
async def get_trade(trade_id: str):
    return await asyncio.to_thread(_get_trade_sync, trade_id)

def _lifecycle_orphan_scan_sync():
    """Run comprehensive orphan scan across all per-strategy lifecycles."""
    if not _engine or not hasattr(_engine, "orphan_scan"):
        return {"error": "Engine lifecycle not initialized"}
    try:
        return _engine.orphan_scan()
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/trades/orphan-scan")
async def lifecycle_orphan_scan():
    return await asyncio.to_thread(_lifecycle_orphan_scan_sync)

def _lifecycle_reconcile_sync():
    """Run lifecycle reconciliation across all per-strategy lifecycles."""
    if not _engine or not hasattr(_engine, "reconcile_trades"):
        return {"error": "Engine lifecycle not initialized"}
    try:
        return _engine.reconcile_trades()
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/trades/lifecycle-reconcile")
async def lifecycle_reconciliation():
    return await asyncio.to_thread(_lifecycle_reconcile_sync)
