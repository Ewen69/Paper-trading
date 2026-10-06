"""In-process pub/sub for live activity. WebSocket clients subscribe; workers publish."""

import asyncio
from typing import Any

Event = dict[str, Any]


class ActivityBus:
    def __init__(self, max_queue: int = 1000) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max = max_queue

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self._max)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: Event) -> None:
        """Call on the event loop. Slow subscribers lose their oldest events, not new ones."""
        for queue in list(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def publish_threadsafe(self, event: Event) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self.publish, event)
