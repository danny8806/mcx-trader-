"""Positions routes - open/closed positions, position details."""
from __future__ import annotations
import asyncio
import time
from typing import Any, Optional
from fastapi import APIRouter

router = APIRouter()
_engine = None
_bus = None


def init(engine, event_bus):
    global _engine, _bus
    _engine = engine
    _bus = event_bus


def _list_positions_sync(status: Optional[str] = "open", instrument: Optional[str] = None):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        pm = _engine.position_manager
        status = (status or "open").lower()
        if status == "closed":
            raw = [p.snapshot() for p in pm.closed_positions]
        elif status == "all":
            raw = [p.snapshot() for p in pm.closed_positions] + [
                v for v in pm.snapshot().get("open_positions", {}).values()
            ]
        else:
            raw = list(pm.snapshot().get("open_positions", {}).values())
        result = []
        for pos in raw:
            if instrument and pos.get("instrument", "").upper() != instrument.upper():
                continue
            result.append(pos)
        return {"positions": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/positions")
async def list_positions(status: Optional[str] = "open", instrument: Optional[str] = None):
    return await asyncio.to_thread(_list_positions_sync, status, instrument)


def _get_position_sync(position_id: str):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        pos = _engine.position_manager.get_position(position_id)
        if not pos:
            return {"error": f"Position {position_id} not found"}
        return pos.snapshot()
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/positions/{position_id}")
async def get_position(position_id: str):
    return await asyncio.to_thread(_get_position_sync, position_id)


def _get_position_pnl_sync(position_id: str):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        pos = _engine.position_manager.get_position(position_id)
        if not pos:
            return {"error": f"Position {position_id} not found"}
        inst = pos.instrument
        strategy_id = pos.strategy_id
        pnl_eng = _engine.pnl_engines.get(strategy_id)
        snap = pos.snapshot()
        return {
            "position": snap,
            "realized_pnl": snap.get("realized_pnl", 0),
            "unrealized_pnl": snap.get("unrealized_pnl", 0),
            "mark_price": snap.get("current_mark"),
            "entry_price": snap.get("average_entry"),
            "quantity": snap.get("quantity", 0),
            "multiplier": snap.get("multiplier", 1.0),
            "margin": snap.get("margin", 0),
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/positions/{position_id}/pnl")
async def get_position_pnl(position_id: str):
    return await asyncio.to_thread(_get_position_pnl_sync, position_id)
