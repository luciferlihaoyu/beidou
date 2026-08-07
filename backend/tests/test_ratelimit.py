"""Rate-limit tests for register, login and init-admin endpoints.

The limiter is in-process global state keyed by client IP + route; the
autouse fixture in conftest resets it before every test, so each case starts
from an empty sliding window.
"""

from conftest import create_admin

REGISTER_LIMIT = 5
LOGIN_LIMIT = 10
INIT_ADMIN_LIMIT = 3


async def test_register_rate_limited(client):
    codes = []
    for i in range(REGISTER_LIMIT + 1):
        r = await client.post(
            "/api/auth/register",
            json={
                "username": f"rluser{i}",
                "email": f"rluser{i}@test.local",
                "password": "secret123",
            },
        )
        codes.append(r.status_code)
    assert codes[:REGISTER_LIMIT] == [201] * REGISTER_LIMIT
    assert codes[REGISTER_LIMIT] == 429


async def test_login_rate_limited(client):
    await create_admin()
    codes = []
    for _ in range(LOGIN_LIMIT + 1):
        r = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        codes.append(r.status_code)
    assert codes[:LOGIN_LIMIT] == [200] * LOGIN_LIMIT
    assert codes[LOGIN_LIMIT] == 429


async def test_init_admin_rate_limited(client):
    # The rate-limit dependency runs before the ALLOW_INIT_ADMIN gate, so even
    # while init-admin is disabled each call still consumes a token:
    # 403 x limit, then 429.
    codes = []
    for _ in range(INIT_ADMIN_LIMIT + 1):
        r = await client.post("/api/auth/init-admin")
        codes.append(r.status_code)
    assert codes[:INIT_ADMIN_LIMIT] == [403] * INIT_ADMIN_LIMIT
    assert codes[INIT_ADMIN_LIMIT] == 429
