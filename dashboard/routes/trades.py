"""Trades routes - tradebook, trade details."""
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
        if _persistence:
            trades = _persistence.get_trades(strategy_id=strategy)
            if instrument:
                trades = [t for t in trades if t.get("instrument") == instrument.upper()]
            return {"trades": trades, "count": len(trades)}
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
        return {"trades": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/trades")
async def list_trades(strategy: Optional[str] = None, instrument: Optional[str] = None):
    return await asyncio.to_thread(_list_trades_sync, strategy, instrument)

def _get_trade_sync(trade_id: str):
    try:
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
