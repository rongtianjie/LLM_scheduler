import asyncio
import heapq
import time
from typing import Optional

from app.models import RequestContext


class PriorityQueue:
    """Async priority queue with configurable concurrency and asyncio.Condition-based waiting."""

    def __init__(self, max_size: int, max_concurrency: int = 1):
        self._heap: list = []
        self._max_size = max_size
        self._max_concurrency = max_concurrency
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        self._processing: set[str] = set()  # request_ids currently being processed

    # ── Public properties ──────────────────────────────────────────

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def waiting_count(self) -> int:
        return len(self._heap)

    @property
    def processing_count(self) -> int:
        return len(self._processing)

    @property
    def is_full(self) -> bool:
        return len(self._heap) >= self._max_size

    @property
    def is_processing(self) -> bool:
        return len(self._processing) > 0

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

    async def wait_for_turn(self, request_id: str, timeout: float | None = None) -> None:
        """Block until this request_id can be processed.

        Up to ``max_concurrency`` requests can be processed simultaneously.
        A request acquires its turn when it is at the front of the heap and
        the number of currently processing requests is below the limit.

        Args:
            timeout: Maximum wait time in seconds. None = wait indefinitely.

        Raises:
            asyncio.TimeoutError: if timeout is reached before getting the turn.
        """
        async with self._lock:
            while True:
                # Check if we can process this request
                can_process = len(self._processing) < self._max_concurrency
                if can_process and self._heap:
                    front = self._heap[0]
                    if front[-1].request_id == request_id:
                        self._processing.add(request_id)
                        heapq.heappop(self._heap)
                        return
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    raise

    async def signal_done(self, request_id: str) -> None:
        """Mark a request as done, allowing the next one to proceed."""
        async with self._lock:
            self._processing.discard(request_id)
            self._condition.notify_all()


# ── Singleton ───────────────────────────────────────────────────────

_queue: Optional[PriorityQueue] = None


def init_queue(max_size: int, max_concurrency: int = 1) -> PriorityQueue:
    global _queue
    _queue = PriorityQueue(max_size, max_concurrency)
    return _queue


def get_queue() -> PriorityQueue:
    assert _queue is not None, "Queue not initialized"
    return _queue
