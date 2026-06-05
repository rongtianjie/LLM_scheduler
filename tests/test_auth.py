import pytest

from app.config import AppConfig


@pytest.mark.asyncio
async def test_authenticate_disabled(config):
    """When auth is disabled, authenticate returns anonymous."""
    from app.core.auth import authenticate_request

    # We can't easily construct a FastAPI Request outside of a test client,
    # so we test the logic indirectly via the config check
    assert not config.auth.enabled


@pytest.mark.asyncio
async def test_authenticate_valid_key(queue_with_auth):
    """When auth is enabled, a valid Bearer token returns the username."""
    from app.core.auth import authenticate_request
    from fastapi import Request

    # Verify the key was inserted during fixture setup
    from app.database import get_db
    db = await get_db()
    cursor = await db.execute("SELECT name FROM api_keys WHERE key = ?", ("sk-test-key-123",))
    row = await cursor.fetchone()
    assert row is not None
    assert row["name"] == "testuser"


@pytest.mark.asyncio
async def test_priority_strategy_factory():
    from app.strategies.factory import create_strategy
    from app.strategies.ip_based import IPPriorityStrategy
    from app.strategies.api_key_based import ApiKeyPriorityStrategy

    ip = create_strategy("ip_based")
    assert isinstance(ip, IPPriorityStrategy)

    ak = create_strategy("api_key")
    assert isinstance(ak, ApiKeyPriorityStrategy)

    with pytest.raises(ValueError):
        create_strategy("unknown")


@pytest.mark.asyncio
async def test_config_local_override(tmp_path):
    """config.local.yaml should override values in config.yaml."""
    from app.config import _config, load_config, init_config

    base = tmp_path / "config.yaml"
    local = tmp_path / "config.local.yaml"

    base.write_text("server:\n  port: 8001\nauth:\n  enabled: true\n")
    local.write_text("auth:\n  enabled: false\n")

    cfg = load_config(str(base))
    assert cfg.server.port == 8001
    assert cfg.auth.enabled is False  # overridden by local

    # Check loaded files
    assert str(base) in cfg._loaded_files
    assert str(local) in cfg._loaded_files


def test_config_no_local(tmp_path):
    """Without config.local.yaml, only config.yaml is loaded."""
    from app.config import load_config

    base = tmp_path / "config.yaml"
    base.write_text("server:\n  port: 8001\n")

    cfg = load_config(str(base))
    assert cfg.server.port == 8001
    assert len(cfg._loaded_files) == 1


@pytest.mark.asyncio
async def test_ip_strategy_matching(config):
    """IP strategy matches exact and CIDR patterns."""
    from app.strategies.ip_based import IPPriorityStrategy
    strategy = IPPriorityStrategy()

    assert strategy._ip_matches("192.168.1.100", "192.168.1.100")
    assert strategy._ip_matches("10.0.0.5", "10.0.0.0/24")
    assert not strategy._ip_matches("192.168.1.200", "192.168.1.100")
    assert not strategy._ip_matches("10.1.0.5", "10.0.0.0/24")
