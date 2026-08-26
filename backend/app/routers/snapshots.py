"""章节快照/存稿点路由：5 个端点（创建/列表/详情/回滚/diff）。

- 前缀: ``/api/novels/{novel_id}/chapters/{chapter_id}/snapshots``
- 权限: 同时校验作品归属与章节归属（``get_owned_chapter``）
- 列表: 仅返回元信息（不返回 content），降低大字段传输
- 详情: 返回完整 content
- 回滚: 调用 service 层，先存 pre_rollback 再覆盖
- diff: 基于纯文本 content_text 渲染，前端用 dangerouslySetInnerHTML 渲染
  b 段支持关键字 ``current``：与当前章节正文对比，避免为「与当前对比」
  临时建快照带来的副作用
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_owned_chapter
from ..models import Chapter, ChapterSnapshot
from ..snapshot_service import create_snapshot, diff_html, restore_snapshot
from ..utils import strip_html

router = APIRouter(
    prefix="/api/novels/{novel_id}/chapters/{chapter_id}/snapshots",
    tags=["snapshots"],
)


# ---------- Schema ----------


class CreateSnapshotIn(BaseModel):
    label: str = Field(default="", max_length=100)


class SnapshotMetaOut(BaseModel):
    """列表项：不含 content（content 通过详情取）。"""

    id: int
    chapter_id: int
    word_count: int
    content_hash: str
    label: str
    trigger: str
    created_at: object = None


class SnapshotDetailOut(SnapshotMetaOut):
    content: str
    content_text: str


# ---------- 内部工具 ----------


def _meta(s: ChapterSnapshot) -> dict:
    return {
        "id": s.id,
        "chapter_id": s.chapter_id,
        "word_count": s.word_count,
        "content_hash": s.content_hash,
        "label": s.label,
        "trigger": s.trigger,
        "created_at": s.created_at,
    }


def _detail(s: ChapterSnapshot) -> dict:
    d = _meta(s)
    d["content"] = s.content
    d["content_text"] = s.content_text
    return d


# ---------- 端点 ----------


@router.post("", response_model=SnapshotDetailOut)
async def create_snapshot_endpoint(
    data: CreateSnapshotIn,
    chapter: Chapter = Depends(get_owned_chapter),
    db: AsyncSession = Depends(get_db),
):
    """创建 manual 快照（用户主动"存为存稿点"）。"""
    snap = await create_snapshot(db, chapter, "manual", label=data.label)
    return _detail(snap)


@router.get("", response_model=list[SnapshotMetaOut])
async def list_snapshots(
    trigger: str | None = None,
    chapter: Chapter = Depends(get_owned_chapter),
    db: AsyncSession = Depends(get_db),
):
    """按 created_at 倒序列出快照（不含 content）。可选 ``?trigger=auto|manual|pre_rollback`` 过滤。"""
    if trigger is not None and trigger not in ("auto", "manual", "pre_rollback"):
        raise HTTPException(400, "trigger 仅支持 auto/manual/pre_rollback")
    query = select(ChapterSnapshot).where(ChapterSnapshot.chapter_id == chapter.id)
    if trigger is not None:
        query = query.where(ChapterSnapshot.trigger == trigger)
    query = query.order_by(ChapterSnapshot.created_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return [_meta(r) for r in rows]


@router.get("/{sid}", response_model=SnapshotDetailOut)
async def get_snapshot(
    sid: int,
    chapter: Chapter = Depends(get_owned_chapter),
    db: AsyncSession = Depends(get_db),
):
    """快照详情：含 content/content_text。"""
    snap = await db.get(ChapterSnapshot, sid)
    if snap is None or snap.chapter_id != chapter.id:
        raise HTTPException(404, "快照不存在")
    return _detail(snap)


@router.post("/{sid}/restore")
async def restore_snapshot_endpoint(
    sid: int,
    chapter: Chapter = Depends(get_owned_chapter),
    db: AsyncSession = Depends(get_db),
):
    """防呆回滚：先存当前内容为 pre_rollback，再把快照写回章节。"""
    pre, restored = await restore_snapshot(db, chapter, sid)
    return {
        "ok": True,
        "pre_rollback_id": pre.id,
        "restored_snapshot": _detail(restored),
    }


@router.get("/{a}/diff/{b}")
async def diff_snapshots(
    a: int,
    b: str,  # 接受整数字符串或 "current" 关键字（FastAPI path 参数原生是 str）
    chapter: Chapter = Depends(get_owned_chapter),
    db: AsyncSession = Depends(get_db),
):
    """对比快照 a 与 b 的纯文本；b="current" 时与当前章节正文对比。

    返回 HtmlDiff 渲染的 table HTML 片段。"current" 关键字让前端无需为
    "与当前对比" 临时建快照即可拿到 diff。
    """
    snap_a = await db.get(ChapterSnapshot, a)
    if snap_a is None or snap_a.chapter_id != chapter.id:
        raise HTTPException(404, f"快照 {a} 不存在")

    if b == "current":
        text_b = strip_html(chapter.content or "")
    elif b.isdigit():
        snap_b = await db.get(ChapterSnapshot, int(b))
        if snap_b is None or snap_b.chapter_id != chapter.id:
            raise HTTPException(404, f"快照 {b} 不存在")
        text_b = snap_b.content_text
    else:
        raise HTTPException(400, f"快照标识 {b} 不合法（应为数字 id 或 'current'）")

    return {"html": diff_html(snap_a.content_text, text_b)}
