"""Shared fixtures and helpers for the Beidou backend test suite.

Environment variables are set at module import time — BEFORE any ``app.*``
module is imported — because ``app.core.config.get_settings`` is
``@lru_cache``-ed and ``app.db.session`` builds a module-level engine from it.
Setting the env vars here guarantees the whole app stack (engine, JWT signing,
init-admin gate) points at a fresh scratch SQLite database, never the real
``data/novelwriter.db``.
"""

import os
import pathlib
import uuid

_TEST_ROOT = pathlib.Path("/tmp/opencode/beidou-test")
_TEST_ROOT.mkdir(parents=True, exist_ok=True)
_DB_FILE = _TEST_ROOT / f"test_{uuid.uuid4().hex}.db"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_FILE}"
os.environ["DEBUG"] = "true"
os.environ["SECRET_KEY"] = "t" * 48
os.environ["JWT_SECRET_KEY"] = "j" * 48
os.environ["ALLOW_INIT_ADMIN"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.ratelimit import reset  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import create_app  # noqa: E402

# Single in-process app instance shared by every test. The app's routes use
# the module-level engine from app.db.session, which already points at the
# scratch database set above.
app = create_app()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Start each test with a clean in-memory rate-limit state."""
    reset()


@pytest.fixture
async def client():
    """Fresh scratch schema + httpx client against the in-process app."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    # Close pooled connections so the next test (running on its own event
    # loop) never reuses a connection created on a closed loop.
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db_files() -> None:
    """Remove scratch SQLite files created during the test run."""
    yield
    for f in _TEST_ROOT.glob("test_*.db*"):
        try:
            f.unlink()
        except OSError:
            pass


# -- API helpers (imported by test modules via `from conftest import ...`) ----


async def register_user(client, username, password="secret123", email=None):
    """Register a user through the API; returns the created UserOut dict."""
    email = email or f"{username}@test.local"
    r = await client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert r.status_code == 201, f"register failed: {r.status_code} {r.text}"
    return r.json()


async def login(client, username, password):
    """Log in through the API; returns (status_code, response_body)."""
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return r.status_code, r.json()


async def create_admin(username="admin", password="admin123", email=None):
    """Insert an approved admin directly into the test DB."""
    from app.core.security import get_password_hash
    from app.models.user import User, UserRole, UserStatus

    email = email or f"{username}@test.local"
    async with async_session_factory() as session:
        session.add(
            User(
                username=username,
                email=email,
                password_hash=get_password_hash(password),
                role=UserRole.admin,
                status=UserStatus.approved,
            )
        )
        await session.commit()
    return {"username": username, "password": password}


def auth_headers(token: str) -> dict:
    """Bearer header dict for an access token."""
    return {"Authorization": f"Bearer {token}"}
