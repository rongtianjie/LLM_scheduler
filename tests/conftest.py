from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.config import AppConfig, BackendConfig


@pytest.fixture(autouse=True)
def _clear_login_failures():
    """Reset login failure tracking before each test to avoid cross-test lockout."""
    from app.api.admin_pages import reset_login_failures
    reset_login_failures()


def make_config(**overrides) -> AppConfig:
    """Create a test config with overrides."""
    config = AppConfig(**overrides)
    config.database.path = ":memory:"
    config.auth.enabled = False
    config.metrics.enabled = False
    return config


@pytest_asyncio.fixture
async def config() -> AppConfig:
    cfg = make_config()
    import app.config as config_module
    config_module._config = cfg
    return cfg


@pytest_asyncio.fixture
async def db(config):
    """Set up an in-memory database."""
    from app.database import init_db
    conn = await init_db(config)
    yield conn


@pytest_asyncio.fixture
async def queue(config):
    """Create a small test queue (max_size=3)."""
    from app.core.queue import init_queue
    q = init_queue(3)
    yield q


@pytest_asyncio.fixture
async def queue_with_auth() -> AppConfig:
    """Config with auth enabled for testing auth flows."""
    cfg = make_config()
    cfg.auth.enabled = True
    import app.config as config_module
    config_module._config = cfg

    from app.database import init_db
    conn = await init_db(cfg)

    # Insert a test API key
    await conn.execute(
        "INSERT INTO api_keys (key, name, priority) VALUES (?, ?, ?)",
        ("sk-test-key-123", "testuser", 50),
    )
    await conn.commit()
    return cfg


@pytest_asyncio.fixture
async def config_with_backends() -> AppConfig:
    """Config with multiple test backends for routing tests."""
    cfg = make_config()
    cfg.backends = [
        BackendConfig(name="openai1", base_url="http://o1", protocols=["openai"], models=["gpt-4"]),
        BackendConfig(name="openai2", base_url="http://o2", protocols=["openai"], models=["*"]),
        BackendConfig(name="anthropic1", base_url="http://a1", protocols=["anthropic"], models=["claude-3"]),
    ]
    import app.config as config_module
    config_module._config = cfg
    from app.core.health_checker import init_health_checker
    init_health_checker()
    return cfg


@pytest_asyncio.fixture
async def rate_limiter():
    """Create a fresh rate limiter instance."""
    from app.core.rate_limiter import RateLimiter
    return RateLimiter()


@pytest_asyncio.fixture
async def priority_queue():
    """Create a fresh priority queue with max_size=10."""
    from app.core.queue import PriorityQueue
    return PriorityQueue(max_size=10, max_concurrency=2)
