"""第三方集成：AList 原生接口（备份/上传/封面存储）与璇玑知识库（预留）。

配置存于 integration_configs 表（每用户一行），密码类字段不出接口。
"""

import io
import json
import zipfile
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alist import AlistClient, AlistError
from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..models import IntegrationConfig, User

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
    alist_root: str = Field(default="", max_length=200)  # 留空 = 直接用该 AList 账户的基本路径作为根目录
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
    config.alist_root = ("/" + data.alist_root.strip().strip("/")) if data.alist_root.strip() else "/"
    config.xuanji_url = data.xuanji_url.strip()
    if data.alist_password:
        config.alist_password = data.alist_password
    if data.xuanji_api_key:
        config.xuanji_api_key = data.xuanji_api_key
    await db.commit()
    return {"ok": True}


def _alist_client(config: IntegrationConfig) -> AlistClient:
    if not (config.alist_url and config.alist_username and config.alist_password):
        raise HTTPException(400, "请先完整填写 AList 地址、账号和密码")
    return AlistClient(config.alist_url, config.alist_username, config.alist_password)


@router.post("/alist/test")
async def test_alist(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """分步诊断 AList：① 登录与读取 → ② 创建目录 → ③ 写入并删除测试文件。"""
    config = await _get_config(user, db)
    client = _alist_client(config)
    root = config.alist_root.strip("/")

    # ① 登录 + 原生接口读取（网络异常已在客户端内统一包装为 AlistError）
    try:
        await client.test()
    except AlistError as exc:
        if exc.status == 0:
            # status==0 表示请求未到达业务层（域名/DNS/连接失败等网络层错误）
            raise HTTPException(400, f"无法连接 AList，请检查地址与网络（{exc.message}）")
        raise HTTPException(400, f"AList 拒绝了登录：{exc.message}。请核对用户名和密码")

    # ② 确认根目录存在（不创建根目录本身——账户通常被限制在某个文件夹内，
    #    碰上级路径会被拒），只在根目录下创建子目录
    try:
        root_exists = await client.exists(root or "/")
    except AlistError as exc:
        raise HTTPException(400, f"登录成功，但检查基本路径失败：{exc.message}")
    if not root_exists:
        raise HTTPException(
            400,
            f"基本路径 /{root} 不存在。请检查「基本路径」设置是否正确，"
            "或先在 AList 文件管理里创建该文件夹",
        )
    try:
        for sub in ("backup", "uploads", "covers"):
            await client.ensure_dirs(f"{root}/{sub}")
    except AlistError as exc:
        raise HTTPException(
            400,
            f"读取正常，但在 /{root} 下创建子目录失败：{exc.message}。该账号可能对此目录没有写入权限，"
            "请到 AList 后台确认该账号的权限设置",
        )

    # ③ 真实写入测试：上传一个小文件再删掉
    probe = f"{root}/.beidou-write-test"
    try:
        await client.put(probe, b"ok", "text/plain")
        await client.remove(probe)
    except AlistError as exc:
        raise HTTPException(
            400,
            f"目录已就绪，但写入测试文件失败：{exc.message}。目标存储可能不允许写入"
            "（如部分网盘挂载），请把该 AList 账户挂载到一个可写的本地存储作为其根目录",
        )

    return {"ok": True, "message": f"连接成功，{config.alist_root}（backup、uploads、covers）已就绪，读写均正常"}


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
    except AlistError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "path": remote, "size": len(data)}


# ---------- 璇玑知识库 ----------

def _xuanji_base(config: IntegrationConfig) -> str:
    if not config.xuanji_url:
        raise HTTPException(400, "请先在「账号设置 → 集成」里填写璇玑地址")
    return config.xuanji_url.rstrip("/")


async def _xuanji_call(config: IntegrationConfig, procedure: str, input_data: dict | None = None):
    """调用璇玑的 tRPC 查询接口（superjson 格式，GET）。返回解包后的 data。"""
    base = _xuanji_base(config)
    url = f"{base}/api/trpc/{procedure}"
    params = {}
    if input_data is not None:
        params["input"] = json.dumps({"json": input_data}, ensure_ascii=False)
    headers = {}
    if config.xuanji_api_key:
        headers["Authorization"] = f"Bearer {config.xuanji_api_key}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接璇玑: {exc.__class__.__name__}")
    if resp.status_code in (401, 403):
        raise HTTPException(
            400,
            "璇玑拒绝了访问（401/403）。请检查 API Key 是否正确"
            "（在璇玑后台 → 智能助手/Agent 中创建 API Key，权限需包含读取）",
        )
    try:
        payload = resp.json()
    except ValueError:
        raise HTTPException(400, f"璇玑返回了无法解析的内容（HTTP {resp.status_code}），请确认地址指向璇玑站点")
    if resp.status_code >= 400 or "error" in payload:
        msg = ""
        try:
            msg = payload["error"]["json"]["message"]
        except (KeyError, TypeError):
            pass
        raise HTTPException(400, f"璇玑接口错误（HTTP {resp.status_code}）：{msg or '未知错误'}")
    # superjson 信封：{"result": {"data": {"json": ...}}}
    try:
        return payload["result"]["data"]["json"]
    except (KeyError, TypeError):
        raise HTTPException(400, "璇玑返回格式不符合预期（非 tRPC/superjson 响应）")


@router.post("/xuanji/test")
async def test_xuanji(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """璇玑连通性测试：ping 公共接口探活 + 带 Key 读知识库树验证权限。"""
    config = await _get_config(user, db)
    base = _xuanji_base(config)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{base}/api/trpc/ping")
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接璇玑: {exc.__class__.__name__}")
    if resp.status_code >= 400:
        raise HTTPException(400, f"璇玑不可达（HTTP {resp.status_code}），请检查地址")
    tree = await _xuanji_call(config, "kb.getTree")
    folders = tree.get("folders", []) if isinstance(tree, dict) else []
    docs = tree.get("documents", []) if isinstance(tree, dict) else []
    return {"ok": True, "message": f"连接成功：{len(folders)} 个文件夹、{len(docs)} 篇文档可读取"}


@router.get("/xuanji/tree")
async def xuanji_tree(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """读取璇玑知识库的文件夹与文档列表。"""
    config = await _get_config(user, db)
    tree = await _xuanji_call(config, "kb.getTree")
    if not isinstance(tree, dict):
        raise HTTPException(400, "璇玑返回格式异常")
    folders = [
        {"id": f.get("id"), "name": f.get("name"), "parentId": f.get("parentId"), "icon": f.get("icon")}
        for f in tree.get("folders", [])
    ]
    documents = [
        {
            "id": d.get("id"),
            "title": d.get("title"),
            "folderId": d.get("folderId"),
            "format": d.get("format"),
            "updatedAt": str(d.get("updatedAt") or ""),
        }
        for d in tree.get("documents", [])
    ]
    return {"folders": folders, "documents": documents}


@router.get("/xuanji/search")
async def xuanji_search(
    q: str = Query(min_length=1, max_length=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按标题搜索璇玑文档。"""
    config = await _get_config(user, db)
    docs = await _xuanji_call(config, "kb.searchDocuments", {"query": q})
    return [
        {"id": d.get("id"), "title": d.get("title"), "folderId": d.get("folderId"), "updatedAt": str(d.get("updatedAt") or "")}
        for d in (docs or [])
    ]


@router.get("/xuanji/document/{doc_id}")
async def xuanji_document(doc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """读取璇玑单篇文档全文。"""
    config = await _get_config(user, db)
    doc = await _xuanji_call(config, "kb.getDocument", {"id": doc_id})
    if not doc:
        raise HTTPException(404, "璇玑中不存在这篇文档")
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "content": doc.get("content") or "",
        "format": doc.get("format"),
    }


class XuanjiImportIn(BaseModel):
    document_id: int
    novel_id: int | None = None
    folder_id: int | None = None


@router.post("/xuanji/import")
async def xuanji_import(data: XuanjiImportIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """把璇玑文档导入为北斗资料库条目。"""
    from ..models import LibraryFolder, LibraryItem

    if data.novel_id is not None:
        from ..deps import get_owned_novel

        await get_owned_novel(data.novel_id, user, db)
    if data.folder_id is not None:
        folder = await db.get(LibraryFolder, data.folder_id)
        if folder is None or folder.novel_id != data.novel_id:
            raise HTTPException(400, "目录不存在")
    config = await _get_config(user, db)
    doc = await _xuanji_call(config, "kb.getDocument", {"id": data.document_id})
    if not doc:
        raise HTTPException(404, "璇玑中不存在这篇文档")
    item = LibraryItem(
        novel_id=data.novel_id,
        folder_id=data.folder_id,
        title=(doc.get("title") or "璇玑文档")[:200],
        content=doc.get("content") or "",
        tags="璇玑导入",
        source="xuanji",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"ok": True, "id": item.id, "title": item.title}
