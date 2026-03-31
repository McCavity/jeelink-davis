"""
Fan-out broadcaster: maintains per-client asyncio queues and the latest reading snapshot.
"""

from __future__ import annotations

import asyncio


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._latest: dict | None = None

    def add_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        self._clients.add(q)
        return q

    def remove_client(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    async def broadcast(self, payload: dict) -> None:
        self._latest = payload
        for q in list(self._clients):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow client — drop oldest event to make room
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait(payload)

    @property
    def latest(self) -> dict | None:
        return self._latest


broadcaster = Broadcaster()
