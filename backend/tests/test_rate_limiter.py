import pytest

from app.notion.rate_limiter import AsyncTokenBucket


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_steady_rate_spaces_out_requests():
    t = FakeTime()
    bucket = AsyncTokenBucket(rate=2, capacity=1, clock=t.clock, sleep=t.sleep)
    for _ in range(5):
        await bucket.acquire()
    # First call is free; the next four each wait 0.5 s at 2 requests/second.
    assert sum(t.slept) == pytest.approx(2.0)


async def test_burst_capacity_is_spent_before_waiting():
    t = FakeTime()
    bucket = AsyncTokenBucket(rate=1, capacity=3, clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        await bucket.acquire()
    assert t.slept == []
    await bucket.acquire()
    assert sum(t.slept) == pytest.approx(1.0)


async def test_idle_time_refills_tokens():
    t = FakeTime()
    bucket = AsyncTokenBucket(rate=2, capacity=1, clock=t.clock, sleep=t.sleep)
    await bucket.acquire()
    t.now += 10
    await bucket.acquire()
    assert t.slept == []


def test_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate=0)
