"""Health endpoint tests."""


async def test_health_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "version" in body
    assert body["database_error"] is None
