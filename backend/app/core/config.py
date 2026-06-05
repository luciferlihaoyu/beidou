"""Application configuration via pydantic-settings."""

import logging
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment/.env file."""

    # App
    APP_NAME: str = "北斗 (Beidou)"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    DATABASE_URL: str = "sqlite+aiosqlite:///./novelwriter.db"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Default Admin
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "admin123"
    DEFAULT_ADMIN_EMAIL: str = "admin@novelwriter.local"

    # AI WebSocket
    OPENCLAW_WS_URL: str = "ws://localhost:4000/ws"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:5173"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    """Return cached Settings singleton."""
    s = Settings()
    # Warn if secret keys are still at defaults
    if s.SECRET_KEY == "change-me-in-production":
        logger.warning(
            "SECRET_KEY is set to the default value. "
            "Set a strong SECRET_KEY in production environment variables."
        )
    if s.JWT_SECRET_KEY == "change-me-in-production":
        logger.warning(
            "JWT_SECRET_KEY is set to the default value. "
            "Set a strong JWT_SECRET_KEY in production environment variables."
        )
    if s.DEFAULT_ADMIN_PASSWORD == "admin123":
        logger.warning(
            "DEFAULT_ADMIN_PASSWORD is set to the default 'admin123'. "
            "Change it via DEFAULT_ADMIN_PASSWORD environment variable."
        )
    return s
