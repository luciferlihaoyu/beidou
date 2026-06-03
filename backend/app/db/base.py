"""SQLAlchemy declarative base — import all models here so create_all discovers them."""

from sqlalchemy.orm import DeclarativeBase

# Import all ORM models so Base.metadata knows about every table
import app.models.agent         # noqa: F401
import app.models.chapter       # noqa: F401
import app.models.knowledge     # noqa: F401
import app.models.model_config  # noqa: F401
import app.models.novel         # noqa: F401
import app.models.setting       # noqa: F401
import app.models.user          # noqa: F401


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass
