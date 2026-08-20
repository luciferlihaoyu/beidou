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
    """分步诊断 AList：① 登录与读取 → ② 校验根目录并创建子目录 → ③ 写入并删除测试文件。"""
    config = await _get_config(user, db)
    client = _alist_client(config)
    root = config.alist_root.strip("/")

    # ① 登录 + WebDAV 读取
    try:
        await client.test()
    except WebDAVError as exc:
        if exc.status == 401:
            raise HTTPException(400, "账号或密码错误（401）。请核对 AList 的用户名和密码")
        if exc.status == 403:
            raise HTTPException(
                400,
                "登录成功但没有 WebDAV 权限（403）。请到 AList 后台 → 用户，确认该账号勾选了「WebDAV 读取」",
            )
        if exc.status == 404:
            raise HTTPException(400, "WebDAV 路径不存在（404）。地址只需填站点根地址（如 https://alist.example.com），程序会自动补 /dav")
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接 AList: {exc.__class__.__name__}")

    # ② 确认根目录存在（不创建根目录本身——账户通常被限制在某个文件夹内，
    #    碰上级路径会被拒 403），只在根目录下创建子目录
    try:
        root_exists = await client.exists(root)
    except WebDAVError as exc:
        if exc.status in (401, 403):
            raise HTTPException(
                400,
                f"没有权限访问 /{root}（{exc.status}）。请到 AList 后台 → 用户，"
                "确认该账号的「基本路径」就是这个文件夹，且勾选了「WebDAV 读取」权限",
            )
        raise HTTPException(400, f"检查根目录失败：{exc}")
    if not root_exists:
        raise HTTPException(
            400,
            f"根目录 /{root} 不存在。如果它是分配给该账户的文件夹，请先在 AList 文件管理里创建它，"
            "或检查账户「基本路径」设置是否指向这里",
        )
    try:
        for sub in ("backup", "uploads", "covers"):
            await client.ensure_dirs(f"{root}/{sub}")
    except WebDAVError as exc:
        if exc.status in (401, 403):
            raise HTTPException(
                400,
                f"读取正常，但在 /{root} 下创建子目录被拒绝（{exc.status}）。请检查：① AList 后台该用户需勾选「WebDAV 管理」权限；"
                "② 如果用了「路径权限/元信息」限制，确认对该路径放行；③ 该路径所在存储（如 115 网盘）是否允许通过 WebDAV 建目录",
            )
        raise HTTPException(400, f"创建目录失败：{exc}")
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"连接中断: {exc.__class__.__name__}")

    # ③ 真实写入测试：上传一个小文件再删掉
    probe = f"{root}/.beidou-write-test"
    try:
        await client.put(probe, b"ok", "text/plain")
        await client.delete(probe)
    except WebDAVError as exc:
        if exc.status in (401, 403):
            raise HTTPException(
                400,
                f"目录已就绪，但写入文件被拒绝（{exc.status}）。该存储（如 115 网盘）可能不允许通过 WebDAV 上传，"
                "建议在 AList 后台换一个支持写入的存储，或把根目录改到本地存储路径",
            )
        raise HTTPException(400, f"写入测试失败：{exc}")
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"连接中断: {exc.__class__.__name__}")

    return {"ok": True, "message": f"连接成功，/{root}/（backup、uploads、covers）已就绪，读写均正常"}


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
    """璇玑连通性测试：带上 API Key 请求，能分辨地址不通与 Key 无效（数据同步待下一步对接）。"""
    config = await _get_config(user, db)
    if not config.xuanji_url:
        raise HTTPException(400, "请先填写璇玑地址")
    headers = {"Authorization": f"Bearer {config.xuanji_api_key}"} if config.xuanji_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(config.xuanji_url.rstrip("/") + "/", headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接璇玑: {exc.__class__.__name__}")
    if resp.status_code in (401, 403):
        raise HTTPException(400, "璇玑拒绝了 API Key（401/403），请检查 Key 是否正确")
    if resp.status_code >= 400:
        raise HTTPException(400, f"璇玑返回 {resp.status_code}")
    return {"ok": True, "message": f"璇玑可达（HTTP {resp.status_code}），资料同步功能将在下一步开通"}
