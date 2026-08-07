"""Alembic environment configuration for async SQLAlchemy."""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# The Alembic CLI does not add the current directory to sys.path, so make the
# backend package importable regardless of how env.py is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402

# Import all models so Base.metadata is populated
from app.models.user import User  # noqa: F401
from app.models.novel import Novel  # noqa: F401
from app.models.chapter import Chapter  # noqa: F401
from app.models.chapter_version import ChapterVersion  # noqa: F401
from app.models.chat_message import ChatMessage  # noqa: F401
from app.models.setting import Setting  # noqa: F401
from app.models.agent import Agent  # noqa: F401
from app.models.knowledge import KnowledgeBase, KnowledgeEntry, KnowledgeRelation  # noqa: F401
from app.models.model_config import ModelConfig  # noqa: F401

settings = get_settings()

config = context.config
if config.config_file_name is not None:
    # Don't disable existing app loggers when migrations run inside the app
    # process (app/db/migrations.py), not just from the alembic CLI.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """Return the migration target URL.

    The programmatic runner (app/db/migrations.py) stashes the requested URL
    in ``config.attributes["migration_url"]`` so it can target a specific
    database even though ``get_settings`` is cached. When that override is
    absent (plain CLI usage) fall back to settings, which honours the
    ``DATABASE_URL`` environment variable over the .env file.
    """
    return config.attributes.get("migration_url") or settings.DATABASE_URL


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _database_url().replace("+aiosqlite", "")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = create_async_engine(_database_url())
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run async migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
