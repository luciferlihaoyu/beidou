"""Admin endpoint tests: role gating and the register -> approve flow."""

from conftest import auth_headers, create_admin, login, register_user


async def test_admin_users_list_requires_admin(client):
    await register_user(client, "alice")
    _, body = await login(client, "alice", "secret123")
    r = await client.get("/api/admin/users", headers=auth_headers(body["access_token"]))
    assert r.status_code == 403
    assert "admin" in r.json()["detail"].lower()


async def test_admin_users_list_rejects_unauthenticated(client):
    r = await client.get("/api/admin/users")
    assert r.status_code == 401


async def test_admin_users_list_as_admin(client):
    await create_admin()
    await register_user(client, "alice")
    _, body = await login(client, "admin", "admin123")
    r = await client.get("/api/admin/users", headers=auth_headers(body["access_token"]))
    assert r.status_code == 200
    usernames = [u["username"] for u in r.json()]
    assert "admin" in usernames
    assert "alice" in usernames


async def test_approve_pending_user_flow(client):
    """register (pending) -> admin approves -> user can log in and use app."""
    alice = await register_user(client, "alice")
    await create_admin()
    _, admin_body = await login(client, "admin", "admin123")
    admin_token = admin_body["access_token"]

    r = await client.put(
        f"/api/admin/users/{alice['id']}/status",
        json={"status": "approved"},
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # Approved user can now access approved-only endpoints.
    _, login_body = await login(client, "alice", "secret123")
    r2 = await client.get("/api/novels", headers=auth_headers(login_body["access_token"]))
    assert r2.status_code == 200
    assert r2.json() == []


async def test_update_status_requires_admin(client):
    alice = await register_user(client, "alice")
    _, body = await login(client, "alice", "secret123")
    r = await client.put(
        f"/api/admin/users/{alice['id']}/status",
        json={"status": "approved"},
        headers=auth_headers(body["access_token"]),
    )
    assert r.status_code == 403
