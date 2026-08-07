"""Chapter version history tests: snapshot-on-content-change, listing,
detail, restore, ownership gating, and cascade deletion."""

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.chapter_version import ChapterVersion
from conftest import auth_headers, create_admin, login, register_user


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
    Returns ``(user_id, headers)``.
    """
    user = await register_user(client, username)
    if admin_token is None:
        await create_admin()
        _, admin_body = await login(client, "admin", "admin123")
        admin_token = admin_body["access_token"]
    await _approve_user(client, user["id"], admin_token)
    _, body = await login(client, username, "secret123")
    return user["id"], auth_headers(body["access_token"])


async def _make_novel(client, headers: dict, title: str = "Test Novel") -> int:
    r = await client.post("/api/novels", json={"title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_chapter(client, headers: dict, novel_id: int, title: str, content: str):
    r = await client.post(
        f"/api/novels/{novel_id}/chapters",
        json={"title": title, "content": content},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _versions_url(novel_id: int, chapter_id: int) -> str:
    return f"/api/novels/{novel_id}/chapters/{chapter_id}/versions"


# ── (a) snapshot trigger ──────────────────────────────────

async def test_put_identical_content_creates_no_version(client):
    _, headers = await _make_approved(client, "alice")
    novel_id = await _make_novel(client, headers)
    ch = await _make_chapter(client, headers, novel_id, title="Title A", content="one two three")

    # Same content -> no snapshot
    r = await client.put(
        f"/api/novels/{novel_id}/chapters/{ch['id']}",
        json={"content": "one two three"},
        headers=headers,
    )
    assert r.status_code == 200
    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    assert r.status_code == 200
    assert r.json() == []

    # Title-only edit -> no snapshot
    r = await client.put(
        f"/api/novels/{novel_id}/chapters/{ch['id']}",
        json={"title": "Title B"},
        headers=headers,
    )
    assert r.status_code == 200
    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    assert r.json() == []


async def test_put_changed_content_snapshots_previous_state(client):
    _, headers = await _make_approved(client, "alice")
    novel_id = await _make_novel(client, headers)
    ch = await _make_chapter(client, headers, novel_id, title="Title A", content="one two three")

    r = await client.put(
        f"/api/novels/{novel_id}/chapters/{ch['id']}",
        json={"content": "four five six"},
        headers=headers,
    )
    assert r.status_code == 200

    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    versions = r.json()
    assert len(versions) == 1
    v = versions[0]
    assert v["version"] == 1
    assert v["title"] == "Title A"       # PRE-update state
    assert v["word_count"] == 3
    assert v["created_by"] == "alice"

    r = await client.get(
        f"{_versions_url(novel_id, ch['id'])}/{v['id']}", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["content"] == "one two three"
    assert r.json()["created_by"] == "alice"


# ── (b) numbering + ordering ──────────────────────────────

async def test_multiple_edits_versioned_and_newest_first(client):
    _, headers = await _make_approved(client, "alice")
    novel_id = await _make_novel(client, headers)
    ch = await _make_chapter(client, headers, novel_id, title="Title A", content="content zero here")

    for i in range(1, 4):
        r = await client.put(
            f"/api/novels/{novel_id}/chapters/{ch['id']}",
            json={"content": f"content version {i} words"},
            headers=headers,
        )
        assert r.status_code == 200

    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    versions = r.json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    # Oldest version captured the state right after creation.
    assert versions[2]["title"] == "Title A"
    assert versions[2]["word_count"] == 3


# ── (c) detail + 404 ──────────────────────────────────────

async def test_get_version_detail_and_unknown_404(client):
    _, headers = await _make_approved(client, "alice")
    novel_id = await _make_novel(client, headers)
    ch = await _make_chapter(client, headers, novel_id, title="Title A", content="captured content abc")
    await client.put(
        f"/api/novels/{novel_id}/chapters/{ch['id']}",
        json={"content": "changed content xyz"},
        headers=headers,
    )

    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    v = r.json()[0]
    r = await client.get(f"{_versions_url(novel_id, ch['id'])}/{v['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["content"] == "captured content abc"
    assert r.json()["title"] == "Title A"
    assert r.json()["created_by"] == "alice"

    r = await client.get(f"{_versions_url(novel_id, ch['id'])}/999999", headers=headers)
    assert r.status_code == 404

    # Unknown chapter / novel
    r = await client.get(_versions_url(novel_id, 999999), headers=headers)
    assert r.status_code == 404


# ── (d) restore ───────────────────────────────────────────

async def test_restore_applies_and_snapshots_pre_restore_state(client):
    _, headers = await _make_approved(client, "alice")
    novel_id = await _make_novel(client, headers)
    ch = await _make_chapter(client, headers, novel_id, title="Title A", content="original content words")

    # One edit -> v1 captures the original state.
    r = await client.put(
        f"/api/novels/{novel_id}/chapters/{ch['id']}",
        json={"title": "Title B", "content": "edited content here"},
        headers=headers,
    )
    assert r.status_code == 200

    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    v1 = r.json()[0]
    assert v1["version"] == 1

    # Restore v1 -> chapter takes the version's title/content/word_count.
    r = await client.post(
        f"{_versions_url(novel_id, ch['id'])}/{v1['id']}/restore",
        headers=headers,
    )
    assert r.status_code == 200
    restored = r.json()
    assert restored["title"] == "Title A"
    assert restored["content"] == "original content words"
    assert restored["word_count"] == 3

    # Restore itself snapshotted the pre-restore state -> count +1.
    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    versions = r.json()
    assert [v["version"] for v in versions] == [2, 1]
    r = await client.get(
        f"{_versions_url(novel_id, ch['id'])}/{versions[0]['id']}", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Title B"
    assert r.json()["content"] == "edited content here"
    assert r.json()["word_count"] == 3


# ── (e) ownership ─────────────────────────────────────────

async def test_versions_ownership_403_and_admin_allowed(client):
    await create_admin()
    _, admin_body = await login(client, "admin", "admin123")
    admin_token = admin_body["access_token"]
    _, owner_headers = await _make_approved(client, "owner", admin_token=admin_token)
    _, other_headers = await _make_approved(client, "other", admin_token=admin_token)

    novel_id = await _make_novel(client, owner_headers)
    ch = await _make_chapter(client, owner_headers, novel_id, title="Title A", content="secret content here")
    await client.put(
        f"/api/novels/{novel_id}/chapters/{ch['id']}",
        json={"content": "updated content now"},
        headers=owner_headers,
    )

    # Unauthenticated -> 401
    assert (await client.get(_versions_url(novel_id, ch["id"]))).status_code == 401

    # Non-owner approved user -> 403 on list / detail / restore.
    assert (await client.get(_versions_url(novel_id, ch["id"]), headers=other_headers)).status_code == 403
    assert (
        await client.get(f"{_versions_url(novel_id, ch['id'])}/1", headers=other_headers)
    ).status_code == 403
    assert (
        await client.post(
            f"{_versions_url(novel_id, ch['id'])}/1/restore", headers=other_headers
        )
    ).status_code == 403

    # Admin is allowed.
    r = await client.get(_versions_url(novel_id, ch["id"]), headers=auth_headers(admin_token))
    assert r.status_code == 200
    assert len(r.json()) == 1
    r = await client.get(f"{_versions_url(novel_id, ch['id'])}/1", headers=auth_headers(admin_token))
    assert r.status_code == 200
    r = await client.post(
        f"{_versions_url(novel_id, ch['id'])}/1/restore", headers=auth_headers(admin_token)
    )
    assert r.status_code == 200
    assert r.json()["content"] == "secret content here"


# ── (f) cascade deletion ──────────────────────────────────

async def test_delete_chapter_removes_versions(client):
    _, headers = await _make_approved(client, "alice")
    novel_id = await _make_novel(client, headers)
    ch = await _make_chapter(client, headers, novel_id, title="Title A", content="content one two")
    await client.put(
        f"/api/novels/{novel_id}/chapters/{ch['id']}",
        json={"content": "changed content now"},
        headers=headers,
    )

    r = await client.delete(f"/api/novels/{novel_id}/chapters/{ch['id']}", headers=headers)
    assert r.status_code == 204

    async with async_session_factory() as session:
        result = await session.execute(select(ChapterVersion))
        assert result.scalars().all() == []

    # And the API agrees: listing the deleted chapter is a 404.
    r = await client.get(_versions_url(novel_id, ch["id"]), headers=headers)
    assert r.status_code == 404
