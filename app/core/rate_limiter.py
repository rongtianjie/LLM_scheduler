""""In-memory sliding window rate limiter for API keys."""

import time
from collections import defaultdict
from typing import Dict, List


class RateLimiter:
    """Memory-based sliding window rate limiter (requests per minute per API key)."""

    def __init__(self):
        self._windows: Dict[str, List[float]] = defaultdict(list)

    def check(self, user_name: str, limit_per_minute: int) -> bool:
        """Return True if the request is allowed, False if rate limited.

        Args:
            user_name: API key owner name.
            limit_per_minute: Max requests per minute (0 = unlimited).
        """
        if limit_per_minute <= 0:
            return True

        now = time.time()
        cutoff = now - 60  # sliding window: last 60 seconds

        window = self._windows[user_name]

        # Remove expired entries
        while window and window[0] < cutoff:
            window.pop(0)

        if len(window) >= limit_per_minute:
            return False

        window.append(now)

        # Periodic cleanup: purge windows that are completely stale
        if now - getattr(self, "_last_cleanup", 0) > 300:
            self._last_cleanup = now
            stale = [k for k, v in self._windows.items()
                     if not v or v[-1] < cutoff]
            for k in stale:
                del self._windows[k]

        return True

    def reset(self, user_name: str) -> None:
        """Reset rate limit window for a user."""
        self._windows.pop(user_name, None)


# Module-level singleton
_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
