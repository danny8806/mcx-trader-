"""Orders routes."""
from __future__ import annotations
import asyncio
import time
from typing import Optional
from fastapi import APIRouter
router = APIRouter()
_engine = None
_bus = None

def init(engine, event_bus):
    global _engine, _bus
    _engine = engine
    _bus = event_bus

def _list_orders_sync(strategy: Optional[str] = None, instrument: Optional[str] = None):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        exec_snap = _engine.execution_engine.snapshot()
        orders_dict = _engine.execution_engine._orders
        result = []
        for oid, order in orders_dict.items():
            o = {
                "order_id": order.order_id,
                "strategy_id": order.strategy_id,
                "instrument": order.instrument,
                "side": order.side,
                "quantity": order.quantity,
                "order_type": order.order_type,
                "price": order.price,
                "state": order.state.value if hasattr(order.state, "value") else str(order.state),
                "filled_quantity": order.filled_quantity,
                "average_fill_price": order.average_fill_price,
                "created_at": order.created_at,
                "updated_at": order.updated_at,
                "reason": order.reason,
            }
            if strategy and order.strategy_id != strategy:
                continue
            if instrument and order.instrument != instrument.upper():
                continue
            result.append(o)
        result.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return {"orders": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/orders")
async def list_orders(strategy: Optional[str] = None, instrument: Optional[str] = None):
    return await asyncio.to_thread(_list_orders_sync, strategy, instrument)

def _get_order_sync(order_id: str):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        order = _engine.execution_engine.get_order(order_id)
        if not order:
            return {"error": f"Order {order_id} not found"}
        return {
            "order_id": order.order_id,
            "strategy_id": order.strategy_id,
            "instrument": order.instrument,
            "side": order.side,
            "quantity": order.quantity,
            "order_type": order.order_type,
            "price": order.price,
            "state": order.state.value if hasattr(order.state, "value") else str(order.state),
            "filled_quantity": order.filled_quantity,
            "average_fill_price": order.average_fill_price,
            "fill_ids": order.fill_ids,
            "created_at": order.created_at,
            "updated_at": order.updated_at,
            "reason": order.reason,
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/orders/{order_id}")
async def get_order(order_id: str):
    return await asyncio.to_thread(_get_order_sync, order_id)

def _list_fills_sync(strategy: Optional[str] = None, instrument: Optional[str] = None):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        fills = _engine.execution_engine.get_fills(strategy_id=strategy, instrument=instrument)
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
                "multiplier": f.multiplier,
            })
        result.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return {"fills": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/fills")
async def list_fills(strategy: Optional[str] = None, instrument: Optional[str] = None):
    return await asyncio.to_thread(_list_fills_sync, strategy, instrument)
