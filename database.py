"""SQLite database setup with SQLAlchemy"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# Use __file__-relative path so DB path works regardless of CWD (e.g. in Zeabur containers)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
_DB_PATH = os.path.join(_DATA_DIR, "novelwriter.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DB_PATH}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Enable WAL mode for better concurrent access
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    os.makedirs(_DATA_DIR, exist_ok=True)
    # Import models so tables are created
    import models.user  # noqa
    import models.settings  # noqa
    Base.metadata.create_all(bind=engine)
    # Seed admin user if not exists
    _seed_admin()


def _seed_admin():
    from sqlalchemy.orm import Session
    from models.user import User
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(username="admin", role="admin", status="active")
            admin.set_password("admin888")
            db.add(admin)
            db.commit()
            print("🔐 Admin user created: admin / admin888")
    finally:
        db.close()
