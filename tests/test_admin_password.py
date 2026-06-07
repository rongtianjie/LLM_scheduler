"""Tests for password hashing, verification, and change API."""
import time

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig


@pytest.fixture
def pwd_client():
    """Create a test client with admin auth enabled and plaintext password."""
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
        # Login first
        c.post("/admin/login", data={"username": "admin", "password": "testpass"})
        yield c


class TestPasswordHashing:
    def test_hash_verify_roundtrip(self):
        """hash_password then verify_password works."""
        from app.core.password import hash_password, verify_password
        hashed = hash_password("my-secret-pw")
        assert hashed != "my-secret-pw"
        assert verify_password("my-secret-pw", hashed)

    def test_verify_wrong_password(self):
        """verify_password returns False for wrong password."""
        from app.core.password import hash_password, verify_password
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_verify_legacy_plaintext(self):
        """verify_password works with legacy plaintext passwords."""
        from app.core.password import verify_password
        assert verify_password("plain", "plain")

    def test_hash_is_bcrypt_format(self):
        """hash_password produces bcrypt formatted hash."""
        from app.core.password import hash_password
        hashed = hash_password("test")
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$") or hashed.startswith("$2y$")

    def test_each_hash_is_unique(self):
        """Each hash is unique due to salt."""
        from app.core.password import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2


class TestPasswordChangeAPI:
    def test_change_password_success(self, pwd_client):
        """PUT /admin/api/admin/password changes password successfully."""
        response = pwd_client.put("/admin/api/admin/password", json={
            "old_password": "testpass",
            "new_password": "newsecurepass",
        })
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_change_password_wrong_current(self, pwd_client):
        """Password change fails with wrong current password."""
        response = pwd_client.put("/admin/api/admin/password", json={
            "old_password": "wrongpass",
            "new_password": "newsecurepass",
        })
        assert response.status_code == 403

    def test_change_password_too_short(self, pwd_client):
        """Password change rejects password < 6 chars."""
        response = pwd_client.put("/admin/api/admin/password", json={
            "old_password": "testpass",
            "new_password": "abc",
        })
        assert response.status_code == 422

    def test_login_after_password_change(self, pwd_client):
        """After changing password, can login with new password."""
        # Change password
        pwd_client.put("/admin/api/admin/password", json={
            "old_password": "testpass",
            "new_password": "newpass123",
        })
        # Logout
        pwd_client.get("/admin/logout")
        # Login with new password
        response = pwd_client.post("/admin/login", data={
            "username": "admin", "password": "newpass123"
        }, follow_redirects=False)
        assert response.status_code == 302

    def test_old_password_stops_working(self, pwd_client):
        """After changing password, old password no longer works."""
        pwd_client.put("/admin/api/admin/password", json={
            "old_password": "testpass",
            "new_password": "newpass123",
        })
        pwd_client.get("/admin/logout")
        response = pwd_client.post("/admin/login", data={
            "username": "admin", "password": "testpass"
        }, follow_redirects=False)
        assert response.status_code == 200  # login page with error
        assert "错误" in response.text or "error" in response.text.lower()


class TestLoginLockout:
    def test_lockout_after_five_failures(self):
        """After 5 failed login attempts, IP is locked out."""
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
            # 5 failed attempts
            for _ in range(5):
                c.post("/admin/login", data={"username": "admin", "password": "wrong"})
            # 6th attempt should show lockout message
            response = c.post("/admin/login", data={"username": "admin", "password": "wrong"})
            assert response.status_code == 200
            text = response.text
            assert "锁定" in text or "lock" in text.lower() or "重试" in text
