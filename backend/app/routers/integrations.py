"""第三方集成：AList WebDAV（备份/上传/封面存储）与璇玑知识库（预留）。

配置存于 integration_configs 表（每用户一行），密码类字段不出接口。
"""

import io
import json
import zipfile
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..models import IntegrationConfig, User
from ..webdav import WebDAVClient, WebDAVError

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


async def _get_config(user: User, db: AsyncSession) -> IntegrationConfig:
    result = await db.execute(select(IntegrationConfig).where(IntegrationConfig.user_id == user.id))
    config = result.scalars().first()
    if config is None:
        config = IntegrationConfig(user_id=user.id)
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


class IntegrationIn(BaseModel):
    alist_url: str = Field(default="", max_length=300)
    alist_username: str = Field(default="", max_length=100)
    alist_password: str = Field(default="", max_length=300)  # 留空 = 不修改
    alist_root: str = Field(default="/beidou", max_length=200)
    xuanji_url: str = Field(default="", max_length=300)
    xuanji_api_key: str = Field(default="", max_length=300)  # 留空 = 不修改


@router.get("")
async def get_integration(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    config = await _get_config(user, db)
    return {
        "alist_url": config.alist_url,
        "alist_username": config.alist_username,
        "alist_root": config.alist_root,
        "has_alist_password": bool(config.alist_password),
        "xuanji_url": config.xuanji_url,
        "has_xuanji_key": bool(config.xuanji_api_key),
    }


@router.put("")
async def save_integration(data: IntegrationIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    config = await _get_config(user, db)
    config.alist_url = data.alist_url.strip()
    config.alist_username = data.alist_username.strip()
    config.alist_root = ("/" + data.alist_root.strip().strip("/")) if data.alist_root.strip() else "/beidou"
    config.xuanji_url = data.xuanji_url.strip()
    if data.alist_password:
        config.alist_password = data.alist_password
    if data.xuanji_api_key:
        config.xuanji_api_key = data.xuanji_api_key
    await db.commit()
    return {"ok": True}


def _alist_client(config: IntegrationConfig) -> WebDAVClient:
    if not (config.alist_url and config.alist_username and config.alist_password):
        raise HTTPException(400, "请先完整填写 AList 地址、账号和密码")
    return WebDAVClient(config.alist_url, config.alist_username, config.alist_password)


@router.post("/alist/test")
async def test_alist(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """测试 AList WebDAV 连通性，并确保目录结构存在（backup/uploads/covers）。"""
    config = await _get_config(user, db)
    client = _alist_client(config)
    root = config.alist_root.strip("/")
    try:
        await client.test()
        for sub in ("", "backup", "uploads", "covers"):
            await client.ensure_dirs(f"{root}/{sub}".strip("/"))
    except WebDAVError as exc:
        if exc.status == 401:
            raise HTTPException(400, "AList 账号或密码错误（401）")
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接 AList: {exc.__class__.__name__}")
    return {"ok": True, "message": f"连接成功，目录 {config.alist_root}/（backup、uploads、covers）已就绪"}


def _sqlite_path() -> str:
    prefix = "sqlite+aiosqlite:///"
    url = settings.db_url
    return url[len(prefix):] if url.startswith(prefix) else ""


@router.post("/alist/backup")
async def backup_to_alist(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """把 SQLite 数据库打包为 zip 上传到 AList 的 {root}/backup/ 目录。"""
    config = await _get_config(user, db)
    client = _alist_client(config)
    db_path = _sqlite_path()
    if not db_path:
        raise HTTPException(400, "当前不是 SQLite 数据库，暂不支持自动备份")

    from pathlib import Path

    path = Path(db_path)
    if not path.exists():
        raise HTTPException(400, "未找到数据库文件")

    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, "beidou.db")
        zf.writestr(
            "manifest.json",
            json.dumps({"app": "beidou", "created_at": now, "format": "sqlite-zip"}, ensure_ascii=False, indent=2),
        )
    data = buf.getvalue()
    remote = f"{config.alist_root.strip('/')}/backup/beidou-{now}.zip"
    try:
        await client.put(remote, data, "application/zip")
    except WebDAVError as exc:
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接 AList: {exc.__class__.__name__}")
    return {"ok": True, "path": remote, "size": len(data)}


# ---------- 璇玑（预留） ----------

@router.post("/xuanji/test")
async def test_xuanji(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """璇玑连通性测试（数据同步待下一步对接）。"""
    config = await _get_config(user, db)
    if not config.xuanji_url:
        raise HTTPException(400, "请先填写璇玑地址")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(config.xuanji_url.rstrip("/") + "/")
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接璇玑: {exc.__class__.__name__}")
    if resp.status_code >= 500:
        raise HTTPException(400, f"璇玑返回 {resp.status_code}")
    return {"ok": True, "message": f"璇玑可达（HTTP {resp.status_code}），资料同步功能将在下一步开通"}
