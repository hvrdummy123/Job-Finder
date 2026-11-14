from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import orjson

log = logging.getLogger(__name__)


class EventBus:
    """In-process pub/sub for SSE fan-out (v1 — no LISTEN/NOTIFY)."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        message = {"type": event_type, "data": data or {}}
        async with self._lock:
            dead: list[asyncio.Queue[dict[str, Any]]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    dead.append(queue)
                    log.warning("Dropping slow SSE subscriber")
            for queue in dead:
                self._subscribers.discard(queue)

    async def sse_stream(self) -> AsyncIterator[bytes]:
        queue = await self.subscribe()
        try:
            yield _sse("connected", {"ok": True})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield _sse(message["type"], message["data"])
                except TimeoutError:
                    yield b": keepalive\n\n"
        finally:
            await self.unsubscribe(queue)


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = orjson.dumps(data).decode("utf-8")
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


bus = EventBus()
