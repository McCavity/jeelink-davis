"""
Fan-out broadcaster: maintains per-client asyncio queues and the latest reading snapshot.
"""

from __future__ import annotations

import asyncio


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._merged: dict = {}   # best-known value for every field seen so far

    def add_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        self._clients.add(q)
        return q

    def remove_client(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    async def broadcast(self, payload: dict) -> None:
        # Merge non-null values so _merged always has the freshest value per field
        self._merged.update({k: v for k, v in payload.items() if v is not None})
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
        return self._merged or None


broadcaster = Broadcaster()
