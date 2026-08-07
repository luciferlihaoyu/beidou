"""AI chat history persistence tests: HTTP history endpoints, ownership
gating, and WebSocket end-to-end persistence."""

from starlette.testclient import TestClient

from conftest import app, auth_headers, create_admin, login, register_user


# -- helpers (mirrors test_chapter_versions.py pattern) ----------------------


async def _approve_user(client, user_id: int, admin_token: str) -> None:
    """Approve a pending user using an admin's token."""
    r = await client.put(
        f"/api/admin/users/{user_id}/status",
        json={"status": "approved"},
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200, r.text


async def _make_approved(client, username: str, admin_token=None):
    """Register + approve + re-login a non-admin user.

    If ``admin_token`` is None an admin is created and used to approve.
    Returns ``(user_id, headers, token)``.
    """
    user = await register_user(client, username)
    if admin_token is None:
        await create_admin()
        _, admin_body = await login(client, "admin", "admin123")
        admin_token = admin_body["access_token"]
    await _approve_user(client, user["id"], admin_token)
    _, body = await login(client, username, "secret123")
    return user["id"], auth_headers(body["access_token"]), body["access_token"]


async def _post_message(client, headers: dict, role: str, content: str, agent_id=None):
    """POST a chat message; returns the created ChatMessageOut dict."""
    r = await client.post(
        "/api/ai/history/messages",
        json={"role": role, "content": content, "agent_id": agent_id},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# -- fake AI streaming client (never touches the network) --------------------


class _FakeAIResponse:
    """Response stand-in: 200 + a canned SSE line stream."""

    status_code = 200

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"Hello "}}]}'
        yield 'data: {"choices":[{"delta":{"content":"world"}}]}'
        yield "data: [DONE]"


class _FakeStream:
    """Async context manager wrapping the fake streaming response."""

    async def __aenter__(self):
        return _FakeAIResponse()

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    """httpx.AsyncClient stand-in whose ``.stream()`` returns the canned stream."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method: str, url: str, **kwargs):
        return _FakeStream()


# ── (a) empty history ─────────────────────────────────────

async def test_empty_history_returns_list(client):
    _, headers, _ = await _make_approved(client, "alice")
    r = await client.get("/api/ai/history", headers=headers)
    assert r.status_code == 200
    assert r.json() == []


# ── (b) POST persists → GET returns it ────────────────────

async def test_post_message_persists_and_history_returns_it(client):
    _, headers, _ = await _make_approved(client, "alice")

    created = await _post_message(client, headers, "user", "你好世界", agent_id=None)
    assert created["role"] == "user"
    assert created["content"] == "你好世界"
    assert created["agent_id"] is None
    assert "id" in created
    assert "created_at" in created

    r = await client.get("/api/ai/history", headers=headers)
    assert r.status_code == 200
    messages = r.json()
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好世界"
    assert messages[0]["agent_id"] is None
    assert messages[0]["created_at"] == created["created_at"]


# ── (c) invalid role → 422 ────────────────────────────────

async def test_post_message_rejects_invalid_role(client):
    _, headers, _ = await _make_approved(client, "alice")

    r = await client.post(
        "/api/ai/history/messages",
        json={"role": "system", "content": "nope"},
        headers=headers,
    )
    assert r.status_code == 422

    r = await client.post(
        "/api/ai/history/messages",
        json={"role": "garbage", "content": "nope"},
        headers=headers,
    )
    assert r.status_code == 422

    # Nothing was persisted.
    r = await client.get("/api/ai/history", headers=headers)
    assert r.json() == []


# ── (d) limit clamp: newest N, oldest-first among them ────

async def test_history_limit_returns_newest_oldest_first(client):
    _, headers, _ = await _make_approved(client, "alice")

    for i, content in enumerate(("first", "second", "third")):
        await _post_message(client, headers, "user", content, agent_id=i + 1)

    r = await client.get("/api/ai/history?limit=2", headers=headers)
    assert r.status_code == 200
    messages = r.json()
    assert [m["content"] for m in messages] == ["second", "third"]
    assert [m["agent_id"] for m in messages] == [2, 3]

    # limit is clamped to 1..500.
    r = await client.get("/api/ai/history?limit=0", headers=headers)
    assert len(r.json()) == 1
    r = await client.get("/api/ai/history?limit=9999", headers=headers)
    assert [m["content"] for m in r.json()] == ["first", "second", "third"]


# ── (e) DELETE clears history ─────────────────────────────

async def test_delete_history_returns_count_and_empties(client):
    _, headers, _ = await _make_approved(client, "alice")

    for content in ("one", "two", "three"):
        await _post_message(client, headers, "assistant", content)

    r = await client.delete("/api/ai/history", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"deleted": 3}

    r = await client.get("/api/ai/history", headers=headers)
    assert r.json() == []


# ── (f) ownership isolation ───────────────────────────────

async def test_history_isolated_between_users(client):
    await create_admin()
    _, admin_body = await login(client, "admin", "admin123")
    admin_token = admin_body["access_token"]
    _, alice_headers, _ = await _make_approved(client, "alice", admin_token=admin_token)
    _, bob_headers, _ = await _make_approved(client, "bob", admin_token=admin_token)

    await _post_message(client, alice_headers, "user", "alice secret")

    r = await client.get("/api/ai/history", headers=bob_headers)
    assert r.json() == []

    # Bob's DELETE must not touch Alice's messages.
    r = await client.delete("/api/ai/history", headers=bob_headers)
    assert r.json() == {"deleted": 0}

    r = await client.get("/api/ai/history", headers=alice_headers)
    messages = r.json()
    assert len(messages) == 1
    assert messages[0]["content"] == "alice secret"


# ── (g) unauthenticated → 401 ─────────────────────────────

async def test_history_requires_auth(client):
    assert (await client.get("/api/ai/history")).status_code == 401
    assert (await client.delete("/api/ai/history")).status_code == 401
    assert (
        await client.post(
            "/api/ai/history/messages",
            json={"role": "user", "content": "x"},
        )
    ).status_code == 401


# ── (h) WebSocket end-to-end persistence ──────────────────

async def test_websocket_persists_user_and_assistant_messages(client, monkeypatch):
    monkeypatch.setattr("app.api.ai.httpx.AsyncClient", _FakeAsyncClient)

    _, headers, token = await _make_approved(client, "wsuser")

    test_client = TestClient(app)
    with test_client.websocket_connect(f"/api/ai/chat?token={token}") as ws:
        ws.send_json({"type": "chat", "content": "你好", "agent_id": None, "messages": []})
        received = []
        while True:
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("done"):
                break
        # The server persists AFTER sending done. A ping/pong round-trip proves
        # the persist finished before we exit (TestClient cancels the app task
        # when the websocket session closes).
        ws.send_json({"type": "ping"})
        received.append(ws.receive_json())

    # Protocol unchanged: chat_response frames then a pong.
    assert all(m["type"] in ("chat_response", "pong") for m in received)
    assert received[-1] == {"type": "pong"}
    assert received[-2] == {"type": "chat_response", "content": "", "done": True}
    assert "".join(
        m.get("content", "") for m in received if m["type"] == "chat_response"
    ) == "Hello world"

    r = await client.get("/api/ai/history", headers=headers)
    assert r.status_code == 200
    messages = r.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"
    assert messages[0]["agent_id"] is None
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Hello world"
    assert messages[1]["agent_id"] is None
