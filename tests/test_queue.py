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
async def test_enqueue_dequeue_order():
    q = PriorityQueue(max_size=5)

    ctx_a = make_context("a", priority=100)
    ctx_b = make_context("b", priority=50)
    ctx_c = make_context("c", priority=1)

    assert await q.enqueue(ctx_a)
    assert await q.enqueue(ctx_b)
    assert await q.enqueue(ctx_c)

    assert q.waiting_count == 3
    assert not q.is_full


@pytest.mark.asyncio
async def test_queue_full_returns_false():
    q = PriorityQueue(max_size=2)

    assert await q.enqueue(make_context("a"))
    assert await q.enqueue(make_context("b"))
    assert not await q.enqueue(make_context("c"))
    assert q.is_full


@pytest.mark.asyncio
async def test_concurrency_limit():
    """Only one request can process at a time."""
    q = PriorityQueue(max_size=5)
    ctx1 = make_context("req-1")
    ctx2 = make_context("req-2")

    assert await q.enqueue(ctx1)
    assert await q.enqueue(ctx2)

    # req-1 is at the front, process it synchronously
    await q.wait_for_turn("req-1")
    assert q.is_processing
    assert q.waiting_count == 1  # req-2 still waiting

    # Signal req-1 done
    await q.signal_done("req-1")

    # req-2 should now proceed
    await q.wait_for_turn("req-2")
    assert q.is_processing

    await q.signal_done("req-2")
    assert not q.is_processing


@pytest.mark.asyncio
async def test_wait_for_turn_blocks_until_front():
    q = PriorityQueue(max_size=5)
    ctx = make_context("my-req", priority=100)
    await q.enqueue(ctx)

    await q.wait_for_turn("my-req")
    assert q.is_processing
    assert q.waiting_count == 0

    await q.signal_done("my-req")


@pytest.mark.asyncio
async def test_singleton():
    from app.core.queue import init_queue, get_queue

    q1 = init_queue(5)
    q2 = get_queue()
    assert q1 is q2
    assert q2.max_size == 5


@pytest.mark.asyncio
async def test_priority_ordering():
    """Higher priority (lower number) gets dequeued before lower priority."""
    q = PriorityQueue(max_size=5)

    low = make_context("low", priority=100)
    high = make_context("high", priority=1)

    await q.enqueue(low)
    await q.enqueue(high)

    # "high" has priority 1 → at the front of the heap → gets served first
    await q.wait_for_turn("high")
    assert q.is_processing

    # "high" finishes → "low" should be next
    await q.signal_done("high")
    await q.wait_for_turn("low")
    assert q.is_processing

    await q.signal_done("low")
    assert not q.is_processing


@pytest.mark.asyncio
async def test_multi_concurrency():
    """With concurrency=2, two requests can be processed simultaneously."""
    q = PriorityQueue(max_size=10, max_concurrency=2)
    ctx1 = make_context("req-1", priority=100)
    ctx2 = make_context("req-2", priority=100)
    ctx3 = make_context("req-3", priority=100)

    await q.enqueue(ctx1)
    await q.enqueue(ctx2)
    await q.enqueue(ctx3)

    # Process req-1 and req-2 simultaneously
    await q.wait_for_turn("req-1")
    await q.wait_for_turn("req-2")
    assert q.is_processing
    assert q.processing_count == 2
    assert q.max_concurrency == 2
    assert q.waiting_count == 1  # req-3 still waiting

    # req-3 should not be able to proceed since all slots are full
    with pytest.raises(asyncio.TimeoutError):
        await q.wait_for_turn("req-3", timeout=0.1)

    # Finish req-1 → one slot frees up
    await q.signal_done("req-1")
    assert q.processing_count == 1

    # Now req-3 should proceed
    await q.wait_for_turn("req-3")
    assert q.processing_count == 2

    await q.signal_done("req-2")
    await q.signal_done("req-3")
    assert not q.is_processing
    assert q.processing_count == 0
