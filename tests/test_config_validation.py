"""Configuration validation tests."""

import pytest
from pydantic import ValidationError

from app.config import ProxyConfig, CorsConfig, LogRetentionConfig, AppConfig, QueueConfig


class TestProxyConfigValidation:
    def test_valid_protocols(self):
        for proto in ("http", "https", "socks5"):
            p = ProxyConfig(protocol=proto)
            assert p.protocol == proto

    def test_invalid_protocol_rejected(self):
        with pytest.raises(ValidationError):
            ProxyConfig(protocol="ftp")

    def test_invalid_port_rejected(self):
        with pytest.raises(ValidationError):
            ProxyConfig(port=70000)

    def test_port_zero_allowed(self):
        p = ProxyConfig(port=0)
        assert p.port == 0

    def test_port_65535_allowed(self):
        p = ProxyConfig(port=65535)
        assert p.port == 65535

    def test_negative_port_rejected(self):
        with pytest.raises(ValidationError):
            ProxyConfig(port=-1)


class TestCorsConfig:
    def test_default_origins(self):
        c = CorsConfig()
        assert c.origins == ["*"]

    def test_custom_origins(self):
        c = CorsConfig(origins=["http://localhost:3000", "https://example.com"])
        assert c.origins == ["http://localhost:3000", "https://example.com"]


class TestLogRetentionConfig:
    def test_defaults(self):
        lr = LogRetentionConfig()
        assert lr.retention_days == 90
        assert lr.max_records == 100_000


class TestQueueConfig:
    def test_default_timeout(self):
        q = QueueConfig()
        assert q.timeout == 300

    def test_custom_timeout(self):
        q = QueueConfig(timeout=120)
        assert q.timeout == 120


class TestAppConfig:
    def test_includes_new_sections(self):
        cfg = AppConfig()
        assert cfg.log_retention is not None
        assert cfg.cors is not None
        assert cfg.log_retention.retention_days == 90
        assert cfg.cors.origins == ["*"]
        assert cfg.admin.session_https_only is False
        assert cfg.queue.timeout == 300
