import asyncio
import heapq
import time
from typing import Optional

from app.models import RequestContext


class PriorityQueue:
    """Async priority queue with concurrency=1 and asyncio.Condition-based waiting."""

    def __init__(self, max_size: int):
        self._heap: list = []
        self._max_size = max_size
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        self._current_processing: Optional[str] = None  # request_id

    # ── Public properties ──────────────────────────────────────────

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def waiting_count(self) -> int:
        return len(self._heap)

    @property
    def is_full(self) -> bool:
        return len(self._heap) >= self._max_size

    @property
    def is_processing(self) -> bool:
        return self._current_processing is not None

    # ── Core operations ────────────────────────────────────────────

    async def enqueue(self, context: RequestContext) -> bool:
        """Try to enqueue a request. Returns False if queue is full."""
        async with self._lock:
            if len(self._heap) >= self._max_size:
                return False
            # heap element: (priority, timestamp, tiebreaker_id, context)
            tiebreaker = id(context)
            heapq.heappush(self._heap, (context.priority, context.timestamp, tiebreaker, context))
            self._condition.notify_all()
            return True

    async def wait_for_turn(self, request_id: str) -> None:
        """Block until this request_id is at the front and no other is processing."""
        async with self._lock:
            while True:
                if self._current_processing is not None or not self._heap:
                    await self._condition.wait()
                    continue
                front = self._heap[0]
                if front[-1].request_id == request_id:
                    self._current_processing = request_id
                    heapq.heappop(self._heap)
                    return
                await self._condition.wait()

    async def signal_done(self, request_id: str) -> None:
        """Mark a request as done, allowing the next one to proceed."""
        async with self._lock:
            if self._current_processing == request_id:
                self._current_processing = None
                self._condition.notify_all()


# ── Singleton ───────────────────────────────────────────────────────

_queue: Optional[PriorityQueue] = None


def init_queue(max_size: int) -> PriorityQueue:
    global _queue
    _queue = PriorityQueue(max_size)
    return _queue


def get_queue() -> PriorityQueue:
    assert _queue is not None, "Queue not initialized"
    return _queue
