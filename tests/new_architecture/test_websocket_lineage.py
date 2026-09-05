"""WebSocket event lineage (Phase 44).

The WS manager must deliver event payloads verbatim with their lineage fields
intact, and the engine/event-bus publisher must attach strategy_id + trade_id
to the events it pushes over /ws.
"""
import asyncio
import json

from dashboard.event_bus import EventBus
from dashboard.ws_manager import ConnectionManager


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, message: str):
        self.sent.append(message)


def test_ws_broadcast_carries_lineage_fields():
    fws = _FakeWS()

    async def run():
        mgr = ConnectionManager()
        mgr.connect("client-a", fws, ["all"])
        await mgr.broadcast("events", [
            {"id": 1, "event_type": "TRADE_OPENED", "strategy_id": "gold_01",
             "trade_id": "t-1", "instrument": "GOLDM"},
        ])
        await mgr.broadcast_to_channel("all", "engine_state",
                                       {"strategy_id": "silver_01", "trade_id": "t-2"})
        mgr.disconnect("client-a")
        assert mgr.active_connections == 0

    asyncio.run(run())
    assert len(fws.sent) == 2
    msg = json.loads(fws.sent[0])
    assert msg["type"] == "events"
    assert msg["data"][0]["strategy_id"] == "gold_01"
    assert msg["data"][0]["trade_id"] == "t-1"
    msg2 = json.loads(fws.sent[1])
    assert msg2["type"] == "engine_state"
    assert msg2["data"]["strategy_id"] == "silver_01"


def test_event_bus_passthrough_keeps_lineage():
    bus = EventBus(max_events=100)
    payload = {"event_type": "TRADE_OPENED", "strategy_id": "gold_01",
               "trade_id": "t-42", "instrument": "GOLDM"}
    bus.publish("TRADE_OPENED", payload)
    recent = bus.get_recent(None, 10)
    assert len(recent) >= 1
    assert recent[0]["data"]["strategy_id"] == "gold_01"
    assert recent[0]["data"]["trade_id"] == "t-42"
    assert recent[0]["event_type"] == "TRADE_OPENED"


def test_ws_channel_routing():
    a, b = _FakeWS(), _FakeWS()

    async def run():
        mgr = ConnectionManager()
        mgr.connect("chan-a", a, ["gold"])
        mgr.connect("chan-b", b, ["silver"])
        await mgr.broadcast_to_channel("gold", "trade", {"strategy_id": "gold_01"})
        assert len(a.sent) == 1
        assert len(b.sent) == 0
        # re-subscribe then cross-deliver
        mgr.subscribe("chan-b", ["gold"])
        await mgr.broadcast_to_channel("gold", "trade", {"strategy_id": "gold_01"})
        assert len(b.sent) == 1

    asyncio.run(run())


def test_ws_messages_are_json_serializable():
    fws = _FakeWS()

    async def run():
        mgr = ConnectionManager()
        mgr.connect("c", fws, ["all"])
        await mgr.broadcast("events", [{"position_id": "p-1", "side": "LONG"}])
        assert mgr.total_messages == 1

    asyncio.run(run())
    json.loads(fws.sent[0])  # must not raise