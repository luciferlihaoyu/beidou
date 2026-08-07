"""Auth endpoint tests: register, login, /me, JWT format, init-admin."""

import jwt

from app.core.config import get_settings
from conftest import auth_headers, create_admin, login, register_user


async def test_register_creates_pending_user(client):
    body = await register_user(client, "alice")
    assert isinstance(body["id"], int)
    assert body["username"] == "alice"
    assert body["status"] == "pending"
    assert body["role"] == "author"


async def test_register_duplicate_username_conflict(client):
    await register_user(client, "alice")
    r = await client.post(
        "/api/auth/register",
        json={"username": "alice", "email": "other@test.local", "password": "secret123"},
    )
    assert r.status_code == 409
    assert "taken" in r.json()["detail"]


async def test_login_wrong_password_rejected(client):
    await register_user(client, "alice")
    code, _ = await login(client, "alice", "wrong-password")
    assert code == 401


async def test_login_pending_user_returns_token(client):
    # NOTE: as implemented, pending accounts are NOT blocked at login — only
    # rejected accounts get 403. Registered users are created as pending.
    await register_user(client, "alice")
    code, body = await login(client, "alice", "secret123")
    assert code == 200
    assert body["access_token"]
    assert body["token_type"] == "bearer"


async def test_pending_user_blocked_at_approved_endpoints(client):
    # Pending users can authenticate, but approved-only routes reject them.
    await register_user(client, "alice")
    _, body = await login(client, "alice", "secret123")
    r = await client.get("/api/novels", headers=auth_headers(body["access_token"]))
    assert r.status_code == 403
    assert "pending" in r.json()["detail"].lower()


async def test_login_rejected_user_denied(client):
    alice = await register_user(client, "alice")
    await create_admin()
    _, admin_body = await login(client, "admin", "admin123")
    r = await client.put(
        f"/api/admin/users/{alice['id']}/status",
        json={"status": "rejected"},
        headers=auth_headers(admin_body["access_token"]),
    )
    assert r.status_code == 200
    code, body = await login(client, "alice", "secret123")
    assert code == 403
    assert "rejected" in body["detail"].lower()


async def test_login_approved_after_admin_approval(client):
    alice = await register_user(client, "alice")
    await create_admin()
    _, admin_body = await login(client, "admin", "admin123")
    r = await client.put(
        f"/api/admin/users/{alice['id']}/status",
        json={"status": "approved"},
        headers=auth_headers(admin_body["access_token"]),
    )
    assert r.status_code == 200
    code, body = await login(client, "alice", "secret123")
    assert code == 200
    assert body["access_token"]


async def test_me_returns_current_user_with_valid_token(client):
    await register_user(client, "alice")
    _, body = await login(client, "alice", "secret123")
    r = await client.get("/api/auth/me", headers=auth_headers(body["access_token"]))
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "alice"
    assert data["email"] == "alice@test.local"
    assert data["status"] == "pending"


async def test_me_requires_token(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_rejects_invalid_token(client):
    r = await client.get("/api/auth/me", headers=auth_headers("not-a-jwt"))
    assert r.status_code == 401


async def test_jwt_sub_is_string(client):
    alice = await register_user(client, "alice")
    _, body = await login(client, "alice", "secret123")
    settings = get_settings()
    payload = jwt.decode(
        body["access_token"],
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert isinstance(payload["sub"], str)
    assert payload["sub"] == str(alice["id"])


async def test_init_admin_disabled_by_default(client):
    r = await client.post("/api/auth/init-admin")
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()


async def test_init_admin_enabled_creates_admin_idempotent(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ALLOW_INIT_ADMIN", True)

    r = await client.post("/api/auth/init-admin")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # Idempotent: second call succeeds even though admin already exists.
    r2 = await client.post("/api/auth/init-admin")
    assert r2.status_code == 200
    assert "already exists" in r2.json()["message"]

    # The bootstrapped admin can log in.
    code, _ = await login(client, "admin", "admin123")
    assert code == 200
