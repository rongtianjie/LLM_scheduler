import time
from collections import deque

import pytest

from app.core.rate_limiter import RateLimiter, get_rate_limiter


class TestRateLimiter:
    def test_allow_when_limit_is_zero(self):
        limiter = RateLimiter()
        # limit=0 means unlimited
        for _ in range(100):
            assert limiter.check("user", 0) is True

    def test_allow_under_limit(self):
        limiter = RateLimiter()
        # 5 req/min → 5 requests within 60s should all pass
        for _ in range(5):
            assert limiter.check("user", 5) is True

    def test_deny_over_limit(self):
        limiter = RateLimiter()
        # 3 req/min
        for _ in range(3):
            assert limiter.check("user", 3) is True
        # 4th should fail
        assert limiter.check("user", 3) is False

    def test_reset_clears_window(self):
        limiter = RateLimiter()
        for _ in range(2):
            limiter.check("user", 2)
        assert limiter.check("user", 2) is False
        limiter.reset("user")
        assert limiter.check("user", 2) is True

    def test_isolated_per_user(self):
        limiter = RateLimiter()
        # Fill up user_a's limit
        for _ in range(2):
            limiter.check("user_a", 2)
        assert limiter.check("user_a", 2) is False
        # user_b should still be allowed
        assert limiter.check("user_b", 2) is True

    def test_sliding_window_expiry(self):
        limiter = RateLimiter()
        # Inject old timestamps to simulate window sliding
        limiter._windows["user"] = deque([time.time() - 61])  # 61s ago → expired
        assert limiter.check("user", 1) is True
        # The old expired entry should be cleaned
        assert len(limiter._windows["user"]) == 1  # only the new one

    def test_periodic_cleanup(self):
        limiter = RateLimiter()
        # Add stale windows
        limiter._windows["stale1"] = deque([time.time() - 120])
        limiter._windows["stale2"] = deque([time.time() - 300])
        # Trigger cleanup by setting _last_cleanup far in the past
        limiter._last_cleanup = 0
        assert limiter.check("fresh", 10) is True
        # stale windows should be removed
        assert "stale1" not in limiter._windows
        assert "stale2" not in limiter._windows


def test_singleton():
    a = get_rate_limiter()
    b = get_rate_limiter()
    assert a is b
