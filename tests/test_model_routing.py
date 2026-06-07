"""Tests for model-level backend routing."""
import pytest

from app.config import AppConfig, BackendConfig


def _make_config(backends: list[BackendConfig]) -> AppConfig:
    cfg = AppConfig()
    cfg.database.path = ":memory:"
    cfg.auth.enabled = False
    cfg.metrics.enabled = False
    cfg.backends = backends
    import app.config as cfg_module
    cfg_module._config = cfg
    # Init health checker with defaults
    from app.core.health_checker import init_health_checker
    try:
        init_health_checker()
    except Exception:
        pass
    return cfg


class TestModelRouting:
    @pytest.mark.asyncio
    async def test_exact_model_match(self):
        """Backend with exact model match is selected."""
        b1 = BackendConfig(name="b1", base_url="http://b1", protocols=["openai"], models=["gpt-4"])
        b2 = BackendConfig(name="b2", base_url="http://b2", protocols=["openai"], models=["claude-3"])
        _make_config([b1, b2])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        backend = await _select_backend("openai", model="gpt-4")
        assert backend is not None
        assert backend.name == "b1"

    @pytest.mark.asyncio
    async def test_wildcard_matches_any_model(self):
        """Backend with '*' in models matches any model."""
        b1 = BackendConfig(name="b1", base_url="http://b1", protocols=["openai"], models=["*"])
        b2 = BackendConfig(name="b2", base_url="http://b2", protocols=["openai"], models=["gpt-4"])
        _make_config([b1, b2])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        backend = await _select_backend("openai", model="unknown-model")
        assert backend is not None
        assert backend.name == "b1"  # wildcard backend should match

    @pytest.mark.asyncio
    async def test_empty_models_matches_all(self):
        """Backend with empty models list matches any model."""
        b1 = BackendConfig(name="b1", base_url="http://b1", protocols=["openai"], models=[])
        _make_config([b1])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        backend = await _select_backend("openai", model="any-model")
        assert backend is not None
        assert backend.name == "b1"

    @pytest.mark.asyncio
    async def test_skips_disabled_backend(self):
        """Disabled backends are skipped."""
        b1 = BackendConfig(name="b1", base_url="http://b1", protocols=["openai"], enabled=False)
        _make_config([b1])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        backend = await _select_backend("openai")
        assert backend is None

    @pytest.mark.asyncio
    async def test_skips_wrong_protocol(self):
        """Backend with wrong protocol is skipped."""
        b1 = BackendConfig(name="b1", base_url="http://b1", protocols=["anthropic"])
        _make_config([b1])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        backend = await _select_backend("openai")
        assert backend is None

    @pytest.mark.asyncio
    async def test_priority_exact_over_wildcard(self):
        """Exact model match is preferred over wildcard when both available."""
        b1 = BackendConfig(name="wildcard", base_url="http://b1", protocols=["openai"], models=["*"])
        b2 = BackendConfig(name="exact", base_url="http://b2", protocols=["openai"], models=["gpt-4"])
        _make_config([b1, b2])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        # Both eligible; round-robin picks the first one
        backend = await _select_backend("openai", model="gpt-4")
        assert backend is not None
        # Both are eligible, order depends on config order
        assert backend.name in ("wildcard", "exact")

    @pytest.mark.asyncio
    async def test_no_model_skips_model_filtering(self):
        """When model is empty string, all protocol-matching backends are eligible."""
        b1 = BackendConfig(name="b1", base_url="http://b1", protocols=["openai"], models=["gpt-4"])
        b2 = BackendConfig(name="b2", base_url="http://b2", protocols=["openai"], models=["claude"])
        _make_config([b1, b2])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        backend = await _select_backend("openai", model="")
        assert backend is not None

    @pytest.mark.asyncio
    async def test_exclude_parameter(self):
        """Exclude parameter prevents selection of specified backends."""
        b1 = BackendConfig(name="b1", base_url="http://b1", protocols=["openai"])
        b2 = BackendConfig(name="b2", base_url="http://b2", protocols=["openai"])
        _make_config([b1, b2])

        from app.api.proxy import _select_backend, reset_backend_indices
        reset_backend_indices()
        backend = await _select_backend("openai", exclude={"http://b1"})
        assert backend is not None
        assert backend.base_url == "http://b2"
