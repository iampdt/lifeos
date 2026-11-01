"""Async token bucket used to keep Notion requests under the documented average rate."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class AsyncTokenBucket:
    """Allows `rate` acquisitions per second on average, with bursts up to `capacity`.

    Waiters queue on a lock, so concurrent callers are served one at a time in arrival order.
    `clock` and `sleep` are injectable so tests can run without real time passing.
    """

    def __init__(
        self,
        rate: float,
        capacity: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0 or capacity < 1:
            raise ValueError("rate must be > 0 and capacity >= 1")
        self._rate = rate
        self._capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    async def acquire(self) -> None:
        async with self._lock:
            self._refill()
            while self._tokens < 1:
                await self._sleep((1 - self._tokens) / self._rate)
                self._refill()
            self._tokens -= 1
