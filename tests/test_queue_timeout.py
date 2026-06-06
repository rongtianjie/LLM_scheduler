"""Queue timeout tests."""

import asyncio
import time

import pytest

from app.core.queue import PriorityQueue
from app.models import RequestContext


def make_context(request_id: str, priority: int = 100) -> RequestContext:
    return RequestContext(
        request_id=request_id,
        priority=priority,
        timestamp=time.time(),
    )


@pytest.mark.asyncio
async def test_wait_for_turn_timeout():
    """wait_for_turn with timeout raises asyncio.TimeoutError."""
    q = PriorityQueue(max_size=5)
    ctx1 = make_context("req-1")
    ctx2 = make_context("req-2")

    await q.enqueue(ctx1)
    await q.enqueue(ctx2)

    # req-1 takes the processing slot
    await q.wait_for_turn("req-1")
    assert q.is_processing

    # req-2 is waiting, but timeout is 0.1s → should raise TimeoutError
    with pytest.raises(asyncio.TimeoutError):
        await q.wait_for_turn("req-2", timeout=0.1)

    # Clean up
    await q.signal_done("req-1")


@pytest.mark.asyncio
async def test_wait_for_turn_no_timeout():
    """wait_for_turn with timeout=None waits indefinitely."""
    q = PriorityQueue(max_size=5)
    ctx1 = make_context("req-1")
    ctx2 = make_context("req-2")

    await q.enqueue(ctx1)
    await q.enqueue(ctx2)

    # req-1 takes the processing slot
    await q.wait_for_turn("req-1")

    # req-2 will wait, but we signal_done in a task after a short delay
    async def release():
        await asyncio.sleep(0.1)
        await q.signal_done("req-1")

    asyncio.ensure_future(release())

    # This should eventually succeed (no timeout)
    await q.wait_for_turn("req-2")
    assert q.is_processing

    await q.signal_done("req-2")
