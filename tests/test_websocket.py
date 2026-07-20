import asyncio
from typing import Any

import pytest

from babymonitorvl.main import relay_websocket_events


class FakeWebSocket:
    def __init__(self, *, block_after: int | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.block_after = block_after
        self.block_forever = asyncio.Event()

    async def send_json(self, event: dict[str, Any]) -> None:
        if self.block_after is not None and len(self.sent) >= self.block_after:
            await self.block_forever.wait()
        self.sent.append(event)

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()


@pytest.mark.asyncio
async def test_websocket_relay_sends_events_heartbeats_and_observes_disconnect() -> None:
    websocket = FakeWebSocket()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await queue.put({"type": "capture", "data": {"sequence": 1}})
    task = asyncio.create_task(
        relay_websocket_events(
            websocket,  # type: ignore[arg-type]
            queue,
            {"type": "status", "data": {"state": "stopped"}},
            heartbeat_seconds=0.01,
            send_timeout_seconds=0.1,
        )
    )

    async def wait_for_heartbeat() -> None:
        while not any(event["type"] == "heartbeat" for event in websocket.sent):
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_heartbeat(), timeout=0.2)
    await websocket.incoming.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(task, timeout=0.2)

    assert [event["type"] for event in websocket.sent[:2]] == ["status", "capture"]
    assert websocket.sent[-1] == {"type": "heartbeat", "data": {}}


@pytest.mark.asyncio
async def test_websocket_relay_bounds_a_backpressured_send() -> None:
    websocket = FakeWebSocket(block_after=1)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await queue.put({"type": "capture", "data": {"sequence": 1}})

    with pytest.raises(TimeoutError):
        await relay_websocket_events(
            websocket,  # type: ignore[arg-type]
            queue,
            {"type": "status", "data": {"state": "stopped"}},
            heartbeat_seconds=1,
            send_timeout_seconds=0.01,
        )

    assert websocket.sent == [{"type": "status", "data": {"state": "stopped"}}]
