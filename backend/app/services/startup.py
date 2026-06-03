"""Startup service: ensure default admin account exists."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.models.user import User, UserRole, UserStatus

logger = logging.getLogger(__name__)


async def ensure_default_admin(db: AsyncSession) -> None:
    """Create the default admin user if it doesn't already exist."""
    settings = get_settings()

    result = await db.execute(
        select(User).where(User.username == settings.DEFAULT_ADMIN_USERNAME)
    )
    if result.scalar_one_or_none():
        return  # Already exists

    admin = User(
        username=settings.DEFAULT_ADMIN_USERNAME,
        email=settings.DEFAULT_ADMIN_EMAIL,
        password_hash=get_password_hash(settings.DEFAULT_ADMIN_PASSWORD),
        role=UserRole.admin,
        status=UserStatus.approved,
    )
    db.add(admin)
    await db.flush()
    logger.info("Default admin user created: %s", settings.DEFAULT_ADMIN_USERNAME)
