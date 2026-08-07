"""Programmatic Alembic migration runner for application startup.

Replaces the old ``Base.metadata.create_all`` bootstrap while staying
backward compatible with databases whose tables were previously created by
``create_all`` (i.e. no ``alembic_version`` table yet). The decision rule is:

* ``alembic_version`` table present  -> ``alembic upgrade head``
* no ``alembic_version`` but app tables exist (``users``) -> ``alembic stamp head``
* empty database                     -> ``alembic upgrade head``

Alembic uses its own engine derived from the target ``DATABASE_URL``, so this
module never touches the application engine from ``app.db.session``.
"""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# backend/ — the parent of the app/ package that contains this module.
_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _make_alembic_config() -> Config:
    """Build an Alembic Config rooted at the backend directory.

    Using absolute paths keeps programmatic migrations working regardless of
    the process current working directory.
    """
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return cfg


async def _database_state(database_url: str) -> tuple[bool, bool]:
    """Inspect the target database.

    Returns ``(has_alembic_version_table, has_users_table)`` using a fresh
    throwaway engine so the application engine is never involved.
    """
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:

            def _get_table_names(sync_conn) -> set[str]:
                return set(inspect(sync_conn).get_table_names())

            table_names = await conn.run_sync(_get_table_names)
            return "alembic_version" in table_names, "users" in table_names
    finally:
        await engine.dispose()


def _apply_migrations(cfg: Config, database_url: str, has_version: bool, has_users: bool) -> str:
    """Run Alembic in a worker thread.

    Alembic's env.py launches its own event loop, so it must not run on the
    (already running) application loop — ``asyncio.to_thread`` gives it a
    thread with no running loop.
    """
    cfg.attributes["migration_url"] = database_url
    if has_version:
        command.upgrade(cfg, "head")
        return "upgraded"
    if has_users:
        command.stamp(cfg, "head")
        return "stamped"
    command.upgrade(cfg, "head")
    return "fresh"


async def run_migrations(database_url: str | None = None) -> str:
    """Apply Alembic migrations to the target database.

    Args:
        database_url: Target database URL. Defaults to the configured
            ``settings.DATABASE_URL`` (the same source the app engine uses).

    Returns:
        The action taken: ``"fresh"`` (empty DB upgraded), ``"stamped"``
        (legacy ``create_all`` DB stamped to head), or ``"upgraded"``
        (already-versioned DB brought up to head).
    """
    url = database_url or get_settings().DATABASE_URL
    cfg = _make_alembic_config()
    has_version, has_users = await _database_state(url)
    action = await asyncio.to_thread(_apply_migrations, cfg, url, has_version, has_users)
    logger.info("Database migration: %s (head applied)", action)
    return action
