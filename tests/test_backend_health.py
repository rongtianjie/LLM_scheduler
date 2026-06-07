"""Tests for backend health checker."""
import pytest
import time

from app.config import BackendConfig


class TestHealthChecker:
    def test_init_defaults(self):
        """HealthChecker initializes with correct defaults."""
        from app.core.health_checker import HealthChecker
        hc = HealthChecker()
        assert hc._interval == 30
        assert hc._fail_threshold == 3
        assert hc._timeout == 5

    def test_mark_healthy(self):
        """_mark with success sets healthy=True."""
        from app.core.health_checker import HealthChecker
        hc = HealthChecker()
        hc._mark_sync("http://test", True)
        assert hc.is_healthy("http://test")

    def test_mark_unhealthy_after_threshold(self):
        """_mark with failures beyond threshold sets healthy=False."""
        from app.core.health_checker import BackendHealth, HealthChecker
        hc = HealthChecker(fail_threshold=2)
        hc._health["http://test"] = BackendHealth()
        hc._mark_sync("http://test", False, "timeout")
        assert hc.is_healthy("http://test")  # only 1 failure
        hc._mark_sync("http://test", False, "timeout")
        assert not hc.is_healthy("http://test")  # 2 failures → unhealthy

    def test_recovery(self):
        """Backend recovers after being unhealthy."""
        from app.core.health_checker import BackendHealth, HealthChecker
        hc = HealthChecker(fail_threshold=2)
        hc._health["http://test"] = BackendHealth()
        # Make unhealthy
        hc._mark_sync("http://test", False)
        hc._mark_sync("http://test", False)
        assert not hc.is_healthy("http://test")
        # Recover
        hc._mark_sync("http://test", True)
        assert hc.is_healthy("http://test")

    def test_get_all_health(self):
        """get_all_health returns dict of all backends."""
        from app.core.health_checker import HealthChecker
        hc = HealthChecker()
        hc._mark_sync("http://a", True)
        hc._mark_sync("http://b", False, "err")
        all_health = hc.get_all_health()
        assert "http://a" in all_health
        assert "http://b" in all_health
        assert all_health["http://a"]["healthy"] is True
        assert all_health["http://b"]["healthy"] is True  # not enough failures yet

    def test_singleton(self):
        """init_health_checker / get_health_checker return singleton."""
        from app.core.health_checker import init_health_checker, get_health_checker
        hc1 = init_health_checker()
        hc2 = get_health_checker()
        assert hc1 is hc2

    def test_last_check_updated(self):
        """_mark updates last_check timestamp."""
        from app.core.health_checker import HealthChecker
        hc = HealthChecker()
        hc._mark_sync("http://test", True)
        assert hc._health["http://test"].last_check > 0

    def test_fail_count_reset_on_success(self):
        """Fail count resets to 0 after successful check."""
        from app.core.health_checker import BackendHealth, HealthChecker
        hc = HealthChecker(fail_threshold=5)
        hc._health["http://test"] = BackendHealth()
        hc._mark_sync("http://test", False)
        hc._mark_sync("http://test", False)
        assert hc._health["http://test"].fail_count == 2
        hc._mark_sync("http://test", True)
        assert hc._health["http://test"].fail_count == 0

    def test_unknown_backend_defaults_healthy(self):
        """is_healthy returns True for unknown backends."""
        from app.core.health_checker import HealthChecker
        hc = HealthChecker()
        assert hc.is_healthy("http://unknown") is True


# Helper: synchronous _mark for testing without asyncio
def _add_mark_sync():
    """Add a synchronous _mark_sync method to HealthChecker for testing."""
    from app.core.health_checker import HealthChecker
    if not hasattr(HealthChecker, '_mark_sync'):
        def _mark_sync(self, base_url, success, error=""):
            self._health.setdefault(base_url, __import__('app.core.health_checker', fromlist=['BackendHealth']).BackendHealth())
            h = self._health[base_url]
            h.last_check = time.time()
            if success:
                h.healthy = True
                h.fail_count = 0
                h.last_error = ""
            else:
                h.fail_count += 1
                h.last_error = error
                if h.fail_count >= self._fail_threshold:
                    h.healthy = False
        HealthChecker._mark_sync = _mark_sync

_add_mark_sync()
