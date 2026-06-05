from prometheus_client import Counter, Gauge, Histogram, generate_latest
from prometheus_client.registry import REGISTRY

from app.config import get_config

# ── Metrics definitions ─────────────────────────────────────────────

requests_total = Counter(
    "gateway_requests_total",
    "Total number of proxy requests",
    ["endpoint", "status_code", "user"],
)

queue_length = Gauge(
    "gateway_queue_length",
    "Current number of requests waiting in the queue",
)

requests_processing = Gauge(
    "gateway_requests_processing",
    "Currently processing a request (0 or 1)",
)

request_duration_seconds = Histogram(
    "gateway_request_duration_seconds",
    "End-to-end request duration in seconds",
    ["endpoint"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

wait_time_seconds = Histogram(
    "gateway_wait_time_seconds",
    "Time spent waiting in queue in seconds",
    ["endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)


def metrics_enabled() -> bool:
    try:
        return get_config().metrics.enabled
    except AssertionError:
        return True


async def get_metrics():
    """Return Prometheus metrics text."""
    return generate_latest(REGISTRY)
