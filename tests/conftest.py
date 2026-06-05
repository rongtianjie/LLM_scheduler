from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.config import AppConfig


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
