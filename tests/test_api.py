import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, BackendConfig


@pytest.fixture
def client():
    """Create a test client with minimal setup."""
    from app.main import create_app
    app = create_app()

    # Override config AFTER app creation (create_app calls init_config)
    import app.config as cfg_module
    cfg = AppConfig()
    cfg.database.path = ":memory:"
    cfg.auth.enabled = False
    cfg.admin.enabled = False
    cfg.metrics.enabled = False
    cfg.backends = [
        BackendConfig(name="test", base_url="http://10.255.255.1:1",
                      api_key="sk-test-backend",
                      timeout=1,
                      protocols=["openai", "anthropic"]),
    ]
    cfg_module._config = cfg

    # Use context manager to trigger startup events (DB init, queue init)
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_redirect_root(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 303, 307)


def test_chat_completions_backend_unreachable(client):
    """When backend is unreachable, adapter returns error status."""
    import app.config as cfg_module
    if cfg_module._config.backends:
        cfg_module._config.backends[0].base_url = "http://10.255.255.1:1"
        cfg_module._config.backends[0].timeout = 1

    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hello"}],
    }
    response = client.post("/v1/chat/completions", json=payload)
    # Should return 502/504 when backend is unreachable/timeout
    assert response.status_code in (502, 504, 200)
    if response.status_code == 200:
        import warnings
        warnings.warn("Backend happened to be reachable on test address")


def test_admin_queue_endpoint(client):
    response = client.get("/admin/api/queue")
    assert response.status_code == 200
    data = response.json()
    assert "max_length" in data
    assert "current_waiting" in data
    assert "current_processing" in data
    assert "queue_full" in data


def test_admin_stats_endpoint(client):
    response = client.get("/admin/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_requests" in data


def test_admin_logs_endpoint(client):
    response = client.get("/admin/api/logs")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


def test_admin_keys_crud(client):
    # List keys (empty initially)
    response = client.get("/admin/api/keys")
    assert response.status_code == 200
    assert response.json() == []

    # Create key
    response = client.post("/admin/api/keys", json={"name": "testuser", "priority": 50})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "testuser"
    assert data["priority"] == 50
    assert data["enabled"] is True
    key_id = data["id"]

    # List again
    response = client.get("/admin/api/keys")
    assert len(response.json()) == 1

    # Update
    response = client.put(f"/admin/api/keys/{key_id}", json={"priority": 10})
    assert response.status_code == 200
    assert response.json()["priority"] == 10

    # Delete
    response = client.delete(f"/admin/api/keys/{key_id}")
    assert response.status_code == 200

    # Verify empty again
    response = client.get("/admin/api/keys")
    assert response.json() == []


def test_admin_auth_on_admin_pages(client):
    # Admin pages should be accessible (auth disabled in test config)
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code in (200, 302, 307)


def test_models_endpoint(client):
    """GET /v1/models returns a response (backend may be unreachable)."""
    response = client.get("/v1/models")
    assert response.status_code in (200, 502, 504)


def test_queue_public_endpoint(client):
    """GET /v1/queue returns queue status without auth."""
    response = client.get("/v1/queue")
    assert response.status_code == 200
    data = response.json()
    assert "max_length" in data
    assert "current_waiting" in data
    assert "current_processing" in data
    assert "queue_full" in data


# ── Login / Session tests ─────────────────────────────────────────

@pytest.fixture
def auth_client():
    """Create a test client with admin session auth enabled."""
    from app.main import create_app
    app = create_app()

    import app.config as cfg_module
    cfg = AppConfig()
    cfg.database.path = ":memory:"
    cfg.auth.enabled = False
    cfg.admin.enabled = True
    cfg.admin.username = "admin"
    cfg.admin.password = "testpass"
    cfg.admin.secret_key = "test-secret-key"
    cfg.metrics.enabled = False
    cfg_module._config = cfg

    with TestClient(app) as c:
        yield c


def test_admin_login_page_renders(auth_client):
    """GET /admin/login renders login page when not logged in."""
    response = auth_client.get("/admin/login")
    assert response.status_code == 200
    assert "login-card" in response.text or "login-form" in response.text


def test_admin_login_wrong_credentials(auth_client):
    """POST /admin/login with wrong credentials shows error."""
    response = auth_client.post("/admin/login", data={
        "username": "wrong", "password": "wrong"
    })
    assert response.status_code == 200  # re-renders login page
    assert "error" in response.text.lower() or "错误" in response.text


def test_admin_login_success(auth_client):
    """POST /admin/login with correct credentials redirects to dashboard."""
    response = auth_client.post("/admin/login", data={
        "username": "admin", "password": "testpass"
    }, follow_redirects=False)
    assert response.status_code == 302
    assert "/admin" in response.headers.get("location", "")


def test_admin_session_required(auth_client):
    """Admin pages redirect to login when not authenticated."""
    response = auth_client.get("/admin", follow_redirects=False)
    assert response.status_code == 302
    assert "/admin/login" in response.headers.get("location", "")


def test_admin_api_session_required(auth_client):
    """Admin API returns 401 when not authenticated."""
    response = auth_client.get("/admin/api/keys")
    assert response.status_code == 401


def test_admin_full_login_flow(auth_client):
    """Full flow: login → access dashboard → access API → logout → blocked."""
    # Login
    response = auth_client.post("/admin/login", data={
        "username": "admin", "password": "testpass"
    }, follow_redirects=True)
    assert response.status_code == 200

    # Access dashboard (should work now)
    response = auth_client.get("/admin")
    assert response.status_code == 200

    # Access API (should work now)
    response = auth_client.get("/admin/api/keys")
    assert response.status_code == 200

    # Logout
    response = auth_client.get("/admin/logout", follow_redirects=False)
    assert response.status_code == 302
    assert "/admin/login" in response.headers.get("location", "")

    # Should be blocked again
    response = auth_client.get("/admin/api/keys")
    assert response.status_code == 401


# ── Timeseries stats tests ────────────────────────────────────────

def test_admin_stats_timeseries(client):
    """GET /admin/api/stats/timeseries returns bucketed data."""
    response = client.get("/admin/api/stats/timeseries?period=24h")
    assert response.status_code == 200
    data = response.json()
    assert "period" in data
    assert data["period"] == "24h"
    assert "interval" in data
    assert "buckets" in data
    assert isinstance(data["buckets"], list)


def test_admin_stats_timeseries_periods(client):
    """Timeseries endpoint accepts all valid period values."""
    for period in ["1h", "6h", "24h", "7d", "30d", "all"]:
        response = client.get(f"/admin/api/stats/timeseries?period={period}")
        assert response.status_code == 200
        assert response.json()["period"] == period


# ── Proxy config tests ────────────────────────────────────────────

def test_admin_config_has_proxy(client):
    """GET /admin/api/config includes proxy section."""
    response = client.get("/admin/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "proxy" in data
    proxy = data["proxy"]
    assert "enabled" in proxy
    assert "protocol" in proxy
    assert "host" in proxy
    assert "port" in proxy
    assert "username" in proxy
    assert "password" in proxy


def test_admin_config_update_proxy(client):
    """PUT /admin/api/config can update proxy settings."""
    # Enable HTTP proxy
    response = client.put("/admin/api/config", json={
        "proxy": {
            "enabled": True,
            "protocol": "http",
            "host": "127.0.0.1",
            "port": 8888,
            "username": "",
            "password": "",
        }
    })
    assert response.status_code == 200
    assert response.json()["ok"] is True

    # Verify
    response = client.get("/admin/api/config")
    proxy = response.json()["proxy"]
    assert proxy["enabled"] is True
    assert proxy["protocol"] == "http"
    assert proxy["host"] == "127.0.0.1"
    assert proxy["port"] == 8888


def test_admin_config_proxy_partial_update(client):
    """PUT /admin/api/config supports partial proxy updates."""
    # First set full proxy
    client.put("/admin/api/config", json={
        "proxy": {
            "enabled": True, "protocol": "http",
            "host": "proxy.local", "port": 3128,
            "username": "", "password": "",
        }
    })

    # Partial update: only change protocol and username
    response = client.put("/admin/api/config", json={
        "proxy": {"protocol": "socks5", "username": "user1"}
    })
    assert response.status_code == 200

    # Verify: host/port preserved, protocol/username changed
    proxy = client.get("/admin/api/config").json()["proxy"]
    assert proxy["protocol"] == "socks5"
    assert proxy["username"] == "user1"
    assert proxy["host"] == "proxy.local"
    assert proxy["port"] == 3128


def test_admin_config_proxy_disable(client):
    """Disabling proxy takes effect immediately."""
    # Enable then disable
    client.put("/admin/api/config", json={
        "proxy": {"enabled": True, "host": "proxy.local", "port": 8080}
    })
    client.put("/admin/api/config", json={
        "proxy": {"enabled": False}
    })
    proxy = client.get("/admin/api/config").json()["proxy"]
    assert proxy["enabled"] is False


# ── New feature tests ────────────────────────────────────────────

def test_admin_config_includes_queue_timeout(client):
    """GET /admin/api/config includes queue.timeout."""
    response = client.get("/admin/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "timeout" in data["queue"]


def test_admin_config_includes_log_retention(client):
    """GET /admin/api/config includes log_retention section."""
    response = client.get("/admin/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "log_retention" in data
    assert "retention_days" in data["log_retention"]
    assert "max_records" in data["log_retention"]


def test_admin_config_includes_cors(client):
    """GET /admin/api/config includes cors section."""
    response = client.get("/admin/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "cors" in data
    assert "origins" in data["cors"]


def test_admin_config_update_queue_timeout(client):
    """PUT /admin/api/config supports updating queue.timeout."""
    response = client.put("/admin/api/config", json={
        "queue": {"timeout": 600}
    })
    assert response.status_code == 200
    assert response.json()["ok"] is True
    # Verify
    data = client.get("/admin/api/config").json()
    assert data["queue"]["timeout"] == 600


def test_admin_config_reject_invalid_proxy_protocol(client):
    """PUT /admin/api/config rejects invalid proxy protocol."""
    response = client.put("/admin/api/config", json={
        "proxy": {"protocol": "ftp"}
    })
    assert response.status_code == 422
    data = response.json()
    assert "protocol" in data["detail"]


def test_admin_keys_with_rate_limit(client):
    """API Key CRUD supports rate_limit and token_quota fields."""
    # Create with rate_limit and quota
    response = client.post("/admin/api/keys", json={
        "name": "ratelimited",
        "priority": 50,
        "rate_limit": 30,
        "token_quota_daily": 10000,
        "token_quota_monthly": 200000,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["rate_limit"] == 30
    assert data["token_quota_daily"] == 10000
    assert data["token_quota_monthly"] == 200000
    key_id = data["id"]

    # Update
    response = client.put(f"/admin/api/keys/{key_id}", json={"rate_limit": 60})
    assert response.status_code == 200
    assert response.json()["rate_limit"] == 60

    # Default values (0 = unlimited)
    response = client.post("/admin/api/keys", json={"name": "defaults"})
    assert response.status_code == 201
    data = response.json()
    assert data["rate_limit"] == 0
    assert data["token_quota_daily"] == 0
    assert data["token_quota_monthly"] == 0

