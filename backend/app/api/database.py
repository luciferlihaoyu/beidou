"""Database management routes — stats, backup."""

import os
import shutil
import time
from urllib.parse import urlparse
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import get_current_admin_user
from app.db.session import engine
from app.models.user import User

router = APIRouter(prefix="/api/database", tags=["database"])

BACKUP_DIR = Path("./data/backups")


@router.get("/stats", response_model=dict)
async def get_stats(
    user: User = Depends(get_current_admin_user),
):
    """Return database statistics (table counts, size)."""
    async with engine.begin() as conn:
        tables = [
            "users", "novels", "chapters", "settings",
            "agents", "model_configs", "knowledge_bases",
            "knowledge_entries", "knowledge_relations",
        ]
        counts = {}
        for table in tables:
            result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            counts[table] = result.scalar()

    # Get DB file size
    settings = get_settings()
    db_url_path = urlparse(settings.DATABASE_URL).path or settings.DATABASE_URL.replace('sqlite+aiosqlite:///', '')
    db_path = Path(db_url_path)
    size_bytes = os.path.getsize(db_path) if db_path.exists() else 0

    return {
        "tables": counts,
        "total_records": sum(counts.values()),
        "db_size_mb": round(size_bytes / (1024 * 1024), 2),
        "db_path": str(db_path),
    }


@router.post("/backup", response_model=dict)
async def trigger_backup(
    user: User = Depends(get_current_admin_user),
):
    """Create a SQLite backup file."""
    settings = get_settings()
    db_url_path = urlparse(settings.DATABASE_URL).path or settings.DATABASE_URL.replace('sqlite+aiosqlite:///', '')
    db_path = Path(db_url_path)
    if not db_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database file not found")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"beidou_backup_{timestamp}.db"
    shutil.copy2(db_path, backup_path)

    return {
        "success": True,
        "backup_path": str(backup_path),
        "size_bytes": backup_path.stat().st_size,
        "timestamp": timestamp,
    }


@router.get("/backups", response_model=dict)
async def list_backups(
    user: User = Depends(get_current_admin_user),
):
    """List all backup files."""
    if not BACKUP_DIR.exists():
        return {"backups": []}

    backups = []
    for f in sorted(BACKUP_DIR.glob("beidou_backup_*.db"), reverse=True):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "size_bytes": stat.st_size,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)),
        })

    return {"backups": backups}
