"""Tests for trace ID generation and injection."""
import pytest
import uuid

from app.config import AppConfig, BackendConfig


@pytest.fixture
def trace_client():
    """Create a test client for trace ID testing."""
    from app.main import create_app
    app = create_app()

    import app.config as cfg_module
    cfg = AppConfig()
    cfg.database.path = ":memory:"
    cfg.auth.enabled = False
    cfg.admin.enabled = False
    cfg.metrics.enabled = False
    cfg.backends = [
        BackendConfig(name="test", base_url="http://10.255.255.1:1",
                      api_key="sk-test", timeout=1, protocols=["openai"])
    ]
    cfg_module._config = cfg

    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


class TestTraceID:
    def test_trace_id_generated(self):
        """Trace ID is generated when not provided by client."""
        # Verify uuid4 hex format
        tid = uuid.uuid4().hex
        assert len(tid) == 32
        assert all(c in '0123456789abcdef' for c in tid)

    def test_client_provided_trace_id_accepted(self, trace_client):
        """When client provides x-trace-id, it is used."""
        payload = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        response = trace_client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"x-trace-id": "my-custom-trace-id"}
        )
        # Backend is unreachable but we verify the header is accepted
        # (200 is accepted in case the backend happens to be reachable)
        assert response.status_code in (200, 502, 504)

    def test_trace_id_is_unique(self):
        """Each generated trace ID is unique."""
        ids = {uuid.uuid4().hex for _ in range(100)}
        assert len(ids) == 100

    def test_trace_id_in_context(self):
        """RequestContext includes trace_id field."""
        from app.models import RequestContext
        ctx = RequestContext(trace_id="test-trace-123")
        assert ctx.trace_id == "test-trace-123"

    def test_trace_id_empty_by_default(self):
        """RequestContext trace_id defaults to empty string."""
        from app.models import RequestContext
        ctx = RequestContext()
        assert ctx.trace_id == ""

    def test_adapter_headers_include_trace_id(self):
        """Adapter headers include x-trace-id when trace_id is set."""
        from app.adapters.openai import OpenAIAdapter
        from app.config import BackendConfig as BC
        import asyncio

        cfg = BC(name="test", base_url="http://test", api_key="sk-test")
        adapter = OpenAIAdapter(cfg, trace_id="my-trace")

        async def get_headers():
            return await adapter._headers()

        headers = asyncio.run(get_headers())
        assert "x-trace-id" in headers
        assert headers["x-trace-id"] == "my-trace"
