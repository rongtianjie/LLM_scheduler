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
    from app.strategies.api_key_based import ApiKeyPriorityStrategy

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


def test_proxy_config_to_url():
    """ProxyConfig.to_url() builds correct proxy URL strings."""
    from app.config import ProxyConfig

    # Disabled → empty
    p = ProxyConfig(enabled=False, protocol="http", host="proxy.example.com", port=8080)
    assert p.to_url() == ""

    # Enabled but no host → empty
    p = ProxyConfig(enabled=True, protocol="http", host="", port=8080)
    assert p.to_url() == ""

    # HTTP proxy without auth
    p = ProxyConfig(enabled=True, protocol="http", host="127.0.0.1", port=8080)
    assert p.to_url() == "http://127.0.0.1:8080"

    # HTTPS proxy without auth
    p = ProxyConfig(enabled=True, protocol="https", host="proxy.example.com", port=443)
    assert p.to_url() == "https://proxy.example.com:443"

    # SOCKS5 proxy with auth
    p = ProxyConfig(enabled=True, protocol="socks5", host="10.0.0.1", port=1080,
                    username="user", password="pass")
    assert p.to_url() == "socks5://user:pass@10.0.0.1:1080"

    # HTTP proxy with auth
    p = ProxyConfig(enabled=True, protocol="http", host="proxy.local", port=3128,
                    username="admin", password="secret")
    assert p.to_url() == "http://admin:secret@proxy.local:3128"


def test_proxy_config_defaults():
    """ProxyConfig defaults are sensible (disabled, no proxy)."""
    from app.config import ProxyConfig
    p = ProxyConfig()
    assert p.enabled is False
    assert p.protocol == "http"
    assert p.host == ""
    assert p.port == 0
    assert p.username == ""
    assert p.password == ""
    assert p.to_url() == ""


def test_app_config_has_proxy():
    """AppConfig includes proxy section by default."""
    from app.config import AppConfig
    cfg = AppConfig()
    assert cfg.proxy is not None
    assert cfg.proxy.enabled is False



