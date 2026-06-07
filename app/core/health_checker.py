"""Backend health checker with periodic probing and auto-failover."""

import asyncio
import time

import httpx
import structlog

from app.config import BackendConfig

logger = structlog.get_logger()


class BackendHealth:
    """Per-backend health state."""

    def __init__(self):
        self.healthy: bool = True
        self.last_check: float = 0.0
        self.fail_count: int = 0
        self.last_error: str = ""


class HealthChecker:
    """Periodically probes backends and tracks health status."""

    def __init__(
        self,
        check_interval: int = 30,
        fail_threshold: int = 3,
        request_timeout: int = 5,
    ):
        self._interval = check_interval
        self._fail_threshold = fail_threshold
        self._timeout = request_timeout
        self._health: dict[str, BackendHealth] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self, backends: list[BackendConfig], proxy_url: str = ""):
        """Start the background health-check loop."""
        async with self._lock:
            for b in backends:
                self._health.setdefault(b.base_url, BackendHealth())
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            trust_env=False,
            proxy=proxy_url or None,
        )
        self._task = asyncio.create_task(self._loop(backends))

    async def stop(self):
        """Stop the health-check loop and close the HTTP client."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _loop(self, backends: list[BackendConfig]):
        """Main probing loop."""
        while True:
            try:
                await self._check_all(backends)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("health_check.loop_error")
            await asyncio.sleep(self._interval)

    async def _check_all(self, backends: list[BackendConfig]):
        """Probe all configured backends."""
        for backend in backends:
            if not backend.enabled:
                continue
            url = f"{backend.base_url}/health"
            try:
                resp = await self._client.get(url)
                await self._mark(backend.base_url, resp.status_code < 500)
            except Exception as e:
                await self._mark(backend.base_url, False, str(e))

    async def _mark(self, base_url: str, success: bool, error: str = ""):
        """Update health state for a backend."""
        async with self._lock:
            h = self._health.setdefault(base_url, BackendHealth())
            h.last_check = time.time()
            if success:
                if not h.healthy:
                    logger.info("health_check.recovered", backend=base_url)
                h.healthy = True
                h.fail_count = 0
                h.last_error = ""
            else:
                h.fail_count += 1
                h.last_error = error
                if h.fail_count >= self._fail_threshold:
                    if h.healthy:
                        logger.warning(
                            "health_check.unhealthy",
                            backend=base_url,
                            fail_count=h.fail_count,
                            error=error,
                        )
                    h.healthy = False

    def is_healthy(self, base_url: str) -> bool:
        """Check if a backend is currently healthy."""
        return self._health.get(base_url, BackendHealth()).healthy

    def get_all_health(self) -> dict:
        """Return health status for all known backends."""
        return {
            url: {
                "healthy": h.healthy,
                "last_check": h.last_check,
                "fail_count": h.fail_count,
                "last_error": h.last_error,
            }
            for url, h in self._health.items()
        }


# ── Module-level singleton ─────────────────────────────────────────

_health_checker: HealthChecker | None = None


def init_health_checker(
    check_interval: int = 30,
    fail_threshold: int = 3,
    request_timeout: int = 5,
) -> HealthChecker:
    global _health_checker
    _health_checker = HealthChecker(check_interval, fail_threshold, request_timeout)
    return _health_checker


def get_health_checker() -> HealthChecker:
    assert _health_checker is not None, "HealthChecker not initialized"
    return _health_checker
