"""Migration runner tests.

These tests invoke ``run_migrations`` directly with a throwaway SQLite file
each — they deliberately avoid the module-level application engine from
``app.db.session`` (the same engine the ``client`` fixture uses).
"""

import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.db.base import Base
from app.db.migrations import _make_alembic_config, run_migrations

_TEST_ROOT = Path("/tmp/opencode/beidou-test")

# Every table the initial migration / Base.metadata should produce.
_ALL_TABLES = {
    "agents",
    "ai_chat_messages",
    "chapter_versions",
    "chapters",
    "knowledge_bases",
    "knowledge_entries",
    "knowledge_relations",
    "model_configs",
    "novels",
    "settings",
    "users",
}


@pytest.fixture
def scratch_db():
    """Yield the path to a fresh throwaway SQLite file, then delete it."""
    _TEST_ROOT.mkdir(parents=True, exist_ok=True)
    db_file = _TEST_ROOT / f"migrations_{uuid.uuid4().hex}.db"
    yield db_file
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(db_file) + suffix).unlink()
        except OSError:
            pass


def _head_revision() -> str:
    """Return the current head revision of the migration chain."""
    cfg = _make_alembic_config()
    return ScriptDirectory.from_config(cfg).get_current_head()


def _table_names(db_file: Path) -> set[str]:
    """Return the set of table names present in a SQLite file."""
    con = sqlite3.connect(db_file)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        con.close()


def _alembic_version(db_file: Path) -> str:
    """Return the single version recorded in alembic_version."""
    con = sqlite3.connect(db_file)
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


async def test_fresh_db_upgraded_to_head(scratch_db):
    action = await run_migrations(database_url=f"sqlite+aiosqlite:///{scratch_db}")

    assert action == "fresh"
    tables = _table_names(scratch_db)
    assert "users" in tables
    assert _ALL_TABLES <= tables
    assert _alembic_version(scratch_db) == _head_revision()


async def test_legacy_create_all_db_is_stamped(scratch_db):
    # Simulate a pre-migration database created by Base.metadata.create_all.
    sync_engine = create_engine(f"sqlite:///{scratch_db}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    assert "users" in _table_names(scratch_db)
    assert "alembic_version" not in _table_names(scratch_db)

    action = await run_migrations(database_url=f"sqlite+aiosqlite:///{scratch_db}")

    assert action == "stamped"
    # Schema untouched: same tables plus the new version table, no duplicates.
    assert _table_names(scratch_db) == _ALL_TABLES | {"alembic_version"}
    assert _alembic_version(scratch_db) == _head_revision()


async def test_already_migrated_db_is_idempotent(scratch_db):
    url = f"sqlite+aiosqlite:///{scratch_db}"

    first = await run_migrations(database_url=url)
    assert first == "fresh"

    second = await run_migrations(database_url=url)
    assert second == "upgraded"
    assert _table_names(scratch_db) == _ALL_TABLES | {"alembic_version"}
    assert _alembic_version(scratch_db) == _head_revision()
