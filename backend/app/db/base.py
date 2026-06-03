"""SQLAlchemy declarative base."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# Import all ORM models AFTER Base is defined, so create_all discovers them.
import app.models.agent         # noqa: E402, F401
import app.models.chapter       # noqa: E402, F401
import app.models.knowledge     # noqa: E402, F401
import app.models.model_config  # noqa: E402, F401
import app.models.novel         # noqa: E402, F401
import app.models.setting       # noqa: E402, F401
import app.models.user          # noqa: E402, F401
