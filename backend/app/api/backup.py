"""Backup management routes — WebDAV remote backup, manual backup, history."""

import json
import os
import shutil
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.security import get_current_admin_user
from app.db.session import engine
from app.models.user import User

router = APIRouter(prefix="/api/backup", tags=["backup"])

BACKUP_DIR = Path("./data/backups")
CONFIG_FILE = Path("./data/backup_config.json")


# ── Config models ───────────────────────────────────────

class WebDAVConfig(BaseModel):
    webdav_url: str = ""
    username: str = ""
    password: str = ""


class BackupResult(BaseModel):
    success: bool
    backup_path: str
    size_bytes: int
    timestamp: str


class WebDAVStatusResult(BaseModel):
    connected: bool
    message: str
    free_space_mb: float | None = None


class BackupHistoryItem(BaseModel):
    filename: str
    size_bytes: int
    created_at: str


# ── Helpers ─────────────────────────────────────────────

def _load_config() -> WebDAVConfig:
    """Load WebDAV config from JSON file."""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return WebDAVConfig(**data)
        except (json.JSONDecodeError, Exception):
            pass
    return WebDAVConfig()


def _save_config(cfg: WebDAVConfig) -> None:
    """Save WebDAV config to JSON file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(cfg.model_dump_json(indent=2))


def _create_local_backup() -> tuple[Path, int]:
    """Create a full backup (DB + data files) and return (path, size)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # Determine DB path
    from app.core.config import get_settings
    settings = get_settings()
    db_url_path = (
        urlparse(settings.DATABASE_URL).path
        or settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    )
    db_path = Path(db_url_path)

    # Ensure absolute path
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    # Build tar.gz
    archive_name = f"beidou_full_backup_{timestamp}.tar.gz"
    archive_path = BACKUP_DIR / archive_name

    with tarfile.open(archive_path, "w:gz") as tar:
        # Add database
        if db_path.exists():
            tar.add(db_path, arcname=db_path.name)

        # Add data directory (novel content, settings, etc.)
        data_dir = Path("./data")
        if data_dir.exists():
            for item in data_dir.rglob("*"):
                if item.is_file() and "backups" not in item.parts:
                    tar.add(item, arcname=str(item))

    return archive_path, archive_path.stat().st_size


# ── Config endpoints ────────────────────────────────────

@router.get("/config", response_model=WebDAVConfig)
async def get_backup_config(
    user: User = Depends(get_current_admin_user),
):
    """Get stored WebDAV backup configuration."""
    return _load_config()


@router.post("/config", response_model=WebDAVConfig)
async def save_backup_config(
    body: WebDAVConfig,
    user: User = Depends(get_current_admin_user),
):
    """Save WebDAV backup configuration."""
    _save_config(body)
    return body


# ── Manual backup ───────────────────────────────────────

@router.post("/manual", response_model=BackupResult)
async def manual_backup(
    user: User = Depends(get_current_admin_user),
):
    """Create a full local backup (database + data files)."""
    try:
        archive_path, size = _create_local_backup()
        return BackupResult(
            success=True,
            backup_path=str(archive_path),
            size_bytes=size,
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup failed: {str(e)}",
        )


# ── Backup history ──────────────────────────────────────

@router.get("/history")
async def backup_history(
    user: User = Depends(get_current_admin_user),
) -> dict:
    """List local backup history."""
    if not BACKUP_DIR.exists():
        return {"backups": []}

    backups: list[dict] = []
    for f in sorted(BACKUP_DIR.glob("beidou_full_backup_*.tar.gz"), reverse=True):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "size_bytes": stat.st_size,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)),
        })

    return {"backups": backups}


# ── WebDAV status check ───────────────────────────────

@router.get("/webdav/status", response_model=WebDAVStatusResult)
async def webdav_status(
    webdav_url: str = Query(..., description="WebDAV server URL"),
    username: str = Query(..., description="WebDAV username"),
    password: str = Query(..., description="WebDAV password"),
    user: User = Depends(get_current_admin_user),
):
    """Check WebDAV server connection status via PROPFIND."""
    url = webdav_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                "PROPFIND",
                url,
                auth=(username, password),
                headers={"Depth": "0"},
            )
            if resp.status_code in (207, 200, 301, 302):
                # Try to get free space from response headers or body
                free_mb: float | None = None
                try:
                    quota_avail = resp.headers.get("X-Quota-Available-Bytes")
                    if quota_avail:
                        free_mb = round(int(quota_avail) / (1024 * 1024), 2)
                except (ValueError, TypeError):
                    pass

                return WebDAVStatusResult(
                    connected=True,
                    message="WebDAV 连接成功",
                    free_space_mb=free_mb,
                )
            elif resp.status_code == 401:
                return WebDAVStatusResult(
                    connected=False,
                    message="认证失败，请检查用户名和密码",
                )
            else:
                return WebDAVStatusResult(
                    connected=False,
                    message=f"连接失败 (HTTP {resp.status_code})",
                )
    except httpx.ConnectError:
        return WebDAVStatusResult(
            connected=False,
            message="无法连接到服务器，请检查 URL",
        )
    except httpx.TimeoutException:
        return WebDAVStatusResult(
            connected=False,
            message="连接超时",
        )
    except Exception as e:
        return WebDAVStatusResult(
            connected=False,
            message=f"连接异常: {str(e)}",
        )


# ── WebDAV backup ──────────────────────────────────────

class WebDAVBackupRequest(BaseModel):
    webdav_url: str
    username: str
    password: str


@router.post("/webdav", response_model=BackupResult)
async def webdav_backup(
    body: WebDAVBackupRequest,
    user: User = Depends(get_current_admin_user),
):
    """Create a backup and upload it to a WebDAV server."""
    # 1. Create local backup
    try:
        archive_path, size = _create_local_backup()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"本地备份失败: {str(e)}",
        )

    # 2. Upload to WebDAV
    url = body.webdav_url.rstrip("/")
    remote_path = f"{url}/{archive_path.name}"

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(archive_path, "rb") as f:
                resp = await client.put(
                    remote_path,
                    content=f.read(),
                    auth=(body.username, body.password),
                )
            if resp.status_code not in (200, 201, 204):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"WebDAV 上传失败 (HTTP {resp.status_code}): {resp.text[:200]}",
                )

    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="无法连接到 WebDAV 服务器",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="上传超时",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"上传异常: {str(e)}",
        )

    return BackupResult(
        success=True,
        backup_path=remote_path,
        size_bytes=size,
        timestamp=datetime.now().isoformat(),
    )
