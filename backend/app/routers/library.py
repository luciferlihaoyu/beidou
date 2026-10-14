"""资料库：公共库 + 小说专属库，目录树 + 文本条目 + 全文搜索 + 文件上传 + AI 辅助整理。"""

import io
import json
import logging
import re
import zipfile

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm_timeouts
from ..db import get_db
from ..deps import get_current_user, get_default_ai_config, get_owned_novel
from ..models import IntegrationConfig, LibraryFolder, LibraryItem, Novel, User

router = APIRouter(prefix="/api/library", tags=["library"])

logger = logging.getLogger("beidou.library")

UPLOAD_MAX_SIZE = 20 * 1024 * 1024  # 单个文件 20MB


# ---------- 作用域校验 ----------

async def _check_scope(novel_id: int | None, user: User, db: AsyncSession) -> None:
    """novel_id 为 None 表示公共库（全用户共享）；否则校验作品归属。"""
    if novel_id is None:
        return
    await get_owned_novel(novel_id, user, db)


def _scope_cond(model, novel_id: int | None):
    return model.novel_id.is_(None) if novel_id is None else model.novel_id == novel_id


# ---------- 目录 ----------

class FolderIn(BaseModel):
    novel_id: int | None = None
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0


class FolderOut(BaseModel):
    id: int
    novel_id: int | None
    parent_id: int | None
    name: str
    sort_order: int

    model_config = {"from_attributes": True}


@router.get("/folders", response_model=list[FolderOut])
async def list_folders(
    novel_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_scope(novel_id, user, db)
    result = await db.execute(
        select(LibraryFolder)
        .where(_scope_cond(LibraryFolder, novel_id))
        .order_by(LibraryFolder.sort_order, LibraryFolder.id)
    )
    return result.scalars().all()


@router.post("/folders", response_model=FolderOut)
async def create_folder(data: FolderIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _check_scope(data.novel_id, user, db)
    if data.parent_id is not None:
        parent = await db.get(LibraryFolder, data.parent_id)
        if parent is None or parent.novel_id != data.novel_id:
            raise HTTPException(400, "父目录不存在")
    folder = LibraryFolder(**data.model_dump())
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return folder


@router.put("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: int, data: FolderIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    folder = await db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "目录不存在")
    await _check_scope(folder.novel_id, user, db)
    if data.parent_id == folder.id:
        raise HTTPException(400, "不能把目录移到自己下面")
    folder.name = data.name
    folder.parent_id = data.parent_id
    folder.sort_order = data.sort_order
    await db.commit()
    await db.refresh(folder)
    return folder


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    folder = await db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "目录不存在")
    await _check_scope(folder.novel_id, user, db)
    # 子目录一并删除（ON DELETE CASCADE），目录内条目移到未归档
    items = (
        await db.execute(select(LibraryItem).where(LibraryItem.folder_id == folder.id))
    ).scalars().all()
    for item in items:
        item.folder_id = None
    await db.delete(folder)
    await db.commit()
    return {"ok": True}


# ---------- 条目 ----------

class ItemIn(BaseModel):
    novel_id: int | None = None
    folder_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    tags: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=500)
    source: str = Field(default="manual", max_length=20)


class ItemOut(ItemIn):
    id: int
    created_at: str
    updated_at: str
    # M2（批F）：璇玑同步元数据**全量透出**——全是非敏感字段（文档 id、两个旗、基线时间戳），
    # 前端要拿它画条目徽标（✓已同步 / ⟳待回写 / ⚠冲突）与「源：璇玑·相对时间」副文本，
    # 缺了就只能靠再发一轮请求去猜。默认值只在手工构造 ItemOut 时兜底，实际出口一律走 _item_out。
    xj_doc_id: str = ""
    xj_local_dirty: bool = False
    xj_conflict: bool = False
    xj_doc_updated_at: str = ""

    model_config = {"from_attributes": True}


def _item_out(item: LibraryItem) -> dict:
    return {
        "id": item.id,
        "novel_id": item.novel_id,
        "folder_id": item.folder_id,
        "title": item.title,
        "content": item.content,
        "tags": item.tags,
        "summary": item.summary,
        "source": item.source,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        # M2（批F）：列表/详情/搜索/导入四处出口共用本函数（见 list_items / create_item /
        # update_item / search 与 import 端点），故四字段一次补齐即全线生效。
        "xj_doc_id": item.xj_doc_id or "",
        "xj_local_dirty": bool(item.xj_local_dirty),
        "xj_conflict": bool(item.xj_conflict),
        "xj_doc_updated_at": item.xj_doc_updated_at or "",
    }


@router.get("/items")
async def list_items(
    novel_id: int | None = Query(default=None),
    folder_id: int | None = Query(default=None),
    unfiled: bool = Query(default=False),
    q: str = Query(default="", max_length=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_scope(novel_id, user, db)
    stmt = select(LibraryItem).where(_scope_cond(LibraryItem, novel_id))
    if unfiled:
        stmt = stmt.where(LibraryItem.folder_id.is_(None))
    elif folder_id is not None:
        # 包含子目录下的条目
        folders = (
            await db.execute(select(LibraryFolder).where(_scope_cond(LibraryFolder, novel_id)))
        ).scalars().all()
        ids = {folder_id}
        changed = True
        while changed:
            changed = False
            for f in folders:
                if f.parent_id in ids and f.id not in ids:
                    ids.add(f.id)
                    changed = True
        stmt = stmt.where(LibraryItem.folder_id.in_(ids))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(LibraryItem.title.like(like), LibraryItem.content.like(like), LibraryItem.tags.like(like))
        )
    stmt = stmt.order_by(LibraryItem.updated_at.desc())
    result = await db.execute(stmt)
    return [_item_out(i) for i in result.scalars().all()]


@router.post("/items", response_model=ItemOut)
async def create_item(data: ItemIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _check_scope(data.novel_id, user, db)
    if data.folder_id is not None:
        folder = await db.get(LibraryFolder, data.folder_id)
        if folder is None or folder.novel_id != data.novel_id:
            raise HTTPException(400, "目录不存在")
    item = LibraryItem(**data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    # B2 FTS 同步
    try:
        from ..search_fts import sync_library_item

        await sync_library_item(db, item.id)
    except Exception:  # noqa: BLE001
        pass
    return _item_out(item)


@router.put("/items/{item_id}", response_model=ItemOut)
async def update_item(
    item_id: int,
    data: ItemIn,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, "条目不存在")
    await _check_scope(item.novel_id, user, db)

    # 批E r3 保存即回写：**只有正文真的变了**才算「本地已改」。
    # 只改标题是给条目起别名（口径 a：标题不回推——璇玑 upsert 以标题为锚，
    # 推别名会在璇玑造副本），故不置 dirty、也不触发后台回写。
    content_changed = (item.content or "") != (data.content or "")

    for key, value in data.model_dump().items():
        if key == "novel_id":
            continue  # 不允许跨库移动
        setattr(item, key, value)

    if str(item.xj_doc_id or "").strip() and content_changed:
        item.xj_local_dirty = True
        config = await _xj_sync_config(user, db)
        if config is not None and config.xj_sync_enabled:
            # 后台任务自建 session（仿 t3 定稿钩子），失败静默——绝不拖慢/搞崩这次保存
            from ..xj_sync import push_after_save

            background_tasks.add_task(push_after_save, item.id, user.id)

    await db.commit()
    await db.refresh(item)
    # B2 FTS 同步
    try:
        from ..search_fts import sync_library_item

        await sync_library_item(db, item.id)
    except Exception:  # noqa: BLE001
        pass
    return _item_out(item)


# ---------- 璇玑同步冲突裁决（批E r4，路由就近条目）----------


async def _xj_sync_config(user: User, db: AsyncSession) -> IntegrationConfig | None:
    """取该用户的集成配置（只读，不新建）。"""
    return (
        (await db.execute(select(IntegrationConfig).where(IntegrationConfig.user_id == user.id)))
        .scalars()
        .first()
    )


class ResolveIn(BaseModel):
    #: keep_local = 本地版强写璇玑；take_xuanji = 拉璇玑版覆盖本地
    action: str = Field(min_length=1, max_length=20)


@router.post("/items/{item_id}/resolve")
async def resolve_item(
    item_id: int,
    data: ResolveIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """人工裁决一条同步冲突（两边都改过）。

    - ``keep_local``：按 E1 把**本地正文**强推回璇玑（标题仍取璇玑当前标题），清冲突
    - ``take_xuanji``：拉璇玑全文覆盖本地 content、刷新基线、清冲突；**标题保留本地别名**
      （口径 a，与入方向覆盖语义一致）

    两种 action 都持 ``_SYNC_LOCK``：它们改的正是全量对账也在改的那几列，不与同步轮次并发。
    非同步条目（``xj_doc_id`` 空）或非冲突条目一律 400——避免误裁正常条目。
    """
    from ..xj_sync import (
        _SYNC_LOCK,
        XuanjiReadError,
        _norm_ts,
        _resolve_xuanji_doc,
        push_item_to_xuanji,
    )

    if data.action not in ("keep_local", "take_xuanji"):
        logger.warning("璇玑冲突裁决被拒（条目 %s）：非法 action=%r", item_id, data.action)
        raise HTTPException(400, "action 必须为 keep_local 或 take_xuanji")

    item = await db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, "条目不存在")
    await _check_scope(item.novel_id, user, db)
    if not str(item.xj_doc_id or "").strip() or not item.xj_conflict:
        logger.warning(
            "璇玑冲突裁决被拒（条目 %s）：xj_doc_id=%r conflict=%s（非冲突条目不许裁）",
            item_id,
            item.xj_doc_id,
            item.xj_conflict,
        )
        raise HTTPException(400, "该条目不是「待裁决的同步冲突」条目")

    config = await _xj_sync_config(user, db)
    if config is None or not config.xuanji_url:
        logger.warning("璇玑冲突裁决被拒（条目 %s）：未配置璇玑地址", item_id)
        raise HTTPException(400, "请先在「账号设置 → 集成」里填写璇玑地址")

    async with _SYNC_LOCK:
        if data.action == "keep_local":
            # 先认下用户的裁决，再强推：推成功会清 dirty；推失败则 dirty 留着等下一轮
            # 批量兜底重试（绝不把「本地改动待回写」这个信息丢掉）；断锚则重新置回冲突。
            item.xj_conflict = False
            await db.commit()
            outcome = await push_item_to_xuanji(item, config, db)
            if outcome == "missing":
                logger.warning("keep_local 未成（条目 %s）：璇玑侧锚已断", item_id)
                raise HTTPException(400, "璇玑侧已不存在这篇文档（锚已断），无法回写；请重新导入")
            if outcome == "failed":
                # C1：读/写通道故障只是「这次没成」，dirty 已保留，下轮批量兜底会自动重推
                logger.warning(
                    "keep_local 未完成（条目 %s）：璇玑读/写通道故障，dirty 已保留待下轮重试",
                    item_id,
                )
            result = {"action": "keep_local", "outcome": outcome, "item_id": item.id}
        else:
            try:
                full = await _resolve_xuanji_doc(config, item.xj_doc_id)
            except XuanjiReadError as exc:
                # C1：读不动 ≠ 文档没了——不能说「已不存在」（那是假话），也不改任何本地状态
                logger.warning("take_xuanji 读取失败（条目 %s）：璇玑读通道故障", item_id, exc_info=True)
                raise HTTPException(503, "璇玑暂时不可读，请稍后重试") from exc
            if full is None:
                logger.warning("take_xuanji 未成（条目 %s）：璇玑侧文档已不存在", item_id)
                raise HTTPException(400, "璇玑侧已不存在这篇文档，无法取其版本")
            item.content = full.get("content") or ""
            item.xj_doc_updated_at = _norm_ts(full.get("updatedAt")) or item.xj_doc_updated_at
            item.xj_conflict = False
            item.xj_local_dirty = False
            await db.commit()
            result = {"action": "take_xuanji", "outcome": "taken", "item_id": item.id}

    try:
        from ..search_fts import sync_library_item

        await sync_library_item(db, item.id)
    except Exception:  # noqa: BLE001  FTS 不可用不影响裁决结果
        pass
    return result


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    item = await db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, "条目不存在")
    await _check_scope(item.novel_id, user, db)
    await db.delete(item)
    await db.commit()
    # B2 FTS 同步（删索引行）
    try:
        from ..search_fts import remove_library_item

        await remove_library_item(db, item_id)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


# ---------- B2 全文搜索（FTS5）----------


@router.get("/search")
async def search_items(
    q: str = Query(default="", max_length=100),
    novel_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """资料库全文搜索：FTS5 + snippet 高亮，按相关度排序。

    novel_id 给了 → 搜「公共库 + 该书专属」；没给 → 搜「公共库 + 本人拥有的
    小说专属库」（他人小说的命中在本层整体丢弃，见下方 P0 越权修复）。
    响应 = ItemOut + snippet（<mark> 高亮命中词）。
    """
    if not q.strip():
        return []
    await _check_scope(novel_id, user, db)
    from ..search_fts import search_library_fts

    # 残余 4：novel_id 缺省时，把可见 scope 集合（本人拥有的小说）**下推进 SQL**，
    # 让 FTS 在「公共库 + 本人小说」的集合里取相关度 top N；否则常见词的 top N 可能
    # 全被他人条目占满，本人条目被挤出，用户搜不到自己的东西。
    own_novel_ids: set[int] | None = None
    if novel_id is None:
        rows = await db.execute(select(Novel.id).where(Novel.user_id == user.id))
        own_novel_ids = set(rows.scalars().all())

    hits = await search_library_fts(db, q, novel_id=novel_id, allowed_novel_ids=own_novel_ids)
    if not hits:
        return []
    # P0 越权修复（IDOR）路由层兜底过滤（纵深防御，保留）：FTS 层已按 scope 下推收窄，
    # 这里再按「公共库 + 本人拥有的小说」核一遍，任何一层失守都不至泄露他人小说专属库全文。
    ids = [h["id"] for h in hits]
    items = (await db.execute(select(LibraryItem).where(LibraryItem.id.in_(ids)))).scalars().all()
    by_id = {i.id: i for i in items}
    out = []
    for h in hits:
        it = by_id.get(h["id"])
        if it is None:
            continue  # FTS 与主表不一致（极端情况），跳过
        if own_novel_ids is not None and it.novel_id is not None and it.novel_id not in own_novel_ids:
            continue  # 他人小说专属库：整条丢弃，id/标题/snippet 一律不出网
        d = _item_out(it)
        d["snippet"] = h["snippet"]
        out.append(d)
    return out


# ---------- 文件上传 ----------

def _decode_text(data: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _extract_docx(data: bytes) -> str:
    from xml.etree import ElementTree

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for para in root.iter(ns + "p"):
        text = "".join(node.text or "" for node in para.iter(ns + "t"))
        if text.strip():
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages if page.extract_text())


def extract_text(filename: str, data: bytes) -> str:
    """按扩展名提取纯文本；不支持的类型抛 400。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md", "markdown", "log", "csv"):
        return _decode_text(data)
    if ext == "docx":
        return _extract_docx(data)
    if ext == "pdf":
        return _extract_pdf(data)
    raise HTTPException(400, f"不支持的文件类型 .{ext}（支持 txt / md / docx / pdf）")


@router.post("/upload")
async def upload_files(
    files: list[UploadFile],
    novel_id: int | None = Query(default=None),
    folder_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传文件并解析为资料条目（txt/md/docx/pdf），一个文件一个条目。"""
    await _check_scope(novel_id, user, db)
    if folder_id is not None:
        folder = await db.get(LibraryFolder, folder_id)
        if folder is None or folder.novel_id != novel_id:
            raise HTTPException(400, "目录不存在")
    created = []
    for file in files:
        data = await file.read()
        if len(data) > UPLOAD_MAX_SIZE:
            raise HTTPException(400, f"文件 {file.filename} 超过 20MB 限制")
        content = extract_text(file.filename or "untitled.txt", data).strip()
        title = (file.filename or "未命名").rsplit(".", 1)[0][:200]
        item = LibraryItem(
            novel_id=novel_id,
            folder_id=folder_id,
            title=title,
            content=content,
            source="import",
        )
        db.add(item)
        created.append(item)
    await db.commit()
    for item in created:
        await db.refresh(item)
    # B2 FTS 同步（批量导入的每个条目）
    try:
        from ..search_fts import sync_library_item

        for item in created:
            await sync_library_item(db, item.id)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "count": len(created), "items": [_item_out(i) for i in created]}


# ---------- AI 辅助整理 ----------

class OrganizeOut(BaseModel):
    summary: str
    tags: list[str]
    suggested_folder: str  # 建议目录路径，如 "素材/历史原型"
    reason: str


@router.post("/items/{item_id}/organize", response_model=OrganizeOut)
async def organize_item(item_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """AI 阅读条目内容，给出摘要、标签和建议归档目录。"""
    item = await db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, "条目不存在")
    await _check_scope(item.novel_id, user, db)
    config = await get_default_ai_config(user, db)

    folders = (
        await db.execute(select(LibraryFolder).where(_scope_cond(LibraryFolder, item.novel_id)))
    ).scalars().all()
    folder_names = "、".join(f.name for f in folders) or "（暂无目录）"

    prompt = (
        "你是资料整理助手。阅读下面的资料条目，完成三件事：\n"
        "1. 写一段 50 字以内的摘要；\n"
        "2. 给出 3-6 个检索标签；\n"
        "3. 从现有目录中选一个最合适的归档目录，都不合适则建议一个新目录名。\n\n"
        f"现有目录：{folder_names}\n\n"
        f"条目标题：{item.title}\n"
        f"条目内容（节选）：\n{item.content[:3000]}\n\n"
        '严格按以下 JSON 格式输出，不要输出任何其他内容：\n'
        '{"summary": "...", "tags": ["..."], "suggested_folder": "...", "reason": "一句话理由"}'
    )
    url = config.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 600,
    }
    try:
        # 素材归类是辅助 LLM 任务：统一走 AUX 口径（默认 10 分钟）
        async with httpx.AsyncClient(timeout=llm_timeouts.openai_timeout(llm_timeouts.aux_seconds())) as client:
            resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {config.api_key}"})
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接 AI 接口: {exc.__class__.__name__}")
    if resp.status_code != 200:
        raise HTTPException(400, f"AI 接口返回 {resp.status_code}")

    text = resp.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise HTTPException(502, "AI 返回格式异常，请重试")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise HTTPException(502, "AI 返回格式异常，请重试")
    return OrganizeOut(
        summary=str(data.get("summary", ""))[:500],
        tags=[str(t)[:30] for t in data.get("tags", [])][:6],
        suggested_folder=str(data.get("suggested_folder", ""))[:100],
        reason=str(data.get("reason", ""))[:200],
    )
