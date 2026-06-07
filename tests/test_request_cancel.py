"""Tests for queue request cancellation."""
import asyncio
import time

import pytest

from app.core.queue import PriorityQueue
from app.models import RequestContext


def _ctx(rid: str, user: str = "testuser", priority: int = 100) -> RequestContext:
    return RequestContext(request_id=rid, priority=priority, user_name=user, timestamp=time.time())


class TestRequestCancel:
    @pytest.mark.asyncio
    async def test_cancel_queued_request(self):
        """Cancel removes a waiting request from the queue."""
        q = PriorityQueue(max_size=5)
        await q.enqueue(_ctx("req-1", "alice"))
        await q.enqueue(_ctx("req-2", "alice"))
        assert q.waiting_count == 2

        cancelled = await q.cancel("req-1", "alice")
        assert cancelled is True
        assert q.waiting_count == 1

    @pytest.mark.asyncio
    async def test_cancel_not_found(self):
        """Cancel returns False for non-existent request."""
        q = PriorityQueue(max_size=5)
        await q.enqueue(_ctx("req-1", "alice"))
        cancelled = await q.cancel("nonexistent", "alice")
        assert cancelled is False
        assert q.waiting_count == 1

    @pytest.mark.asyncio
    async def test_cancel_wrong_user(self):
        """Cancel returns False when user doesn't match."""
        q = PriorityQueue(max_size=5)
        await q.enqueue(_ctx("req-1", "alice"))
        cancelled = await q.cancel("req-1", "bob")
        assert cancelled is False
        assert q.waiting_count == 1

    @pytest.mark.asyncio
    async def test_cancel_notifies_waiters(self):
        """Cancelling a request notifies waiters via condition."""
        q = PriorityQueue(max_size=5, max_concurrency=1)
        await q.enqueue(_ctx("req-1", "alice"))
        await q.enqueue(_ctx("req-2", "alice"))

        # Start processing req-1
        await q.wait_for_turn("req-1")
        assert q.is_processing

        # Cancel req-2 (still waiting)
        cancelled = await q.cancel("req-2", "alice")
        assert cancelled is True
        assert q.waiting_count == 0

        await q.signal_done("req-1")

    @pytest.mark.asyncio
    async def test_cancel_empty_queue(self):
        """Cancel on empty queue returns False."""
        q = PriorityQueue(max_size=5)
        cancelled = await q.cancel("any", "user")
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_cancel_middle_of_heap(self):
        """Cancel removes item from middle of heap (not just front)."""
        q = PriorityQueue(max_size=10)
        await q.enqueue(_ctx("a", "u", 100))
        await q.enqueue(_ctx("b", "u", 50))
        await q.enqueue(_ctx("c", "u", 1))

        # Cancel b which is in the middle
        cancelled = await q.cancel("b", "u")
        assert cancelled is True
        assert q.waiting_count == 2

        # Verify heap is still valid — lowest priority (1) should dequeue first
        await q.wait_for_turn("c")  # priority 1
        await q.signal_done("c")
        await q.wait_for_turn("a")  # priority 100
        await q.signal_done("a")

    @pytest.mark.asyncio
    async def test_cancel_releases_slot(self):
        """After cancel, another request can take the slot."""
        q = PriorityQueue(max_size=3)
        await q.enqueue(_ctx("a", "u"))
        await q.enqueue(_ctx("b", "u"))
        await q.enqueue(_ctx("c", "u"))
        assert q.is_full

        cancelled = await q.cancel("b", "u")
        assert cancelled is True
        assert not q.is_full

        # Should be able to enqueue again
        assert await q.enqueue(_ctx("d", "u"))
