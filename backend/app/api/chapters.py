"""Chapter CRUD + reorder routes."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_approved_user
from app.db.session import get_db
from app.models.chapter import Chapter
from app.models.novel import Novel
from app.models.user import User
from app.schemas.content import ChapterCreate, ChapterOut, ChapterReorder, ChapterUpdate

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["chapters"])


async def _get_owned_novel(novel_id: int, user: User, db: AsyncSession) -> Novel:
    """Fetch a novel ensuring the requesting user owns it (or is admin)."""
    result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novel not found")
    if novel.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return novel


@router.get("", response_model=List[ChapterOut])
async def list_chapters(
    novel_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """List all chapters in a novel, ordered by order_index."""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Chapter).where(Chapter.novel_id == novel_id).order_by(Chapter.order_index)
    )
    return result.scalars().all()


@router.post("", response_model=ChapterOut, status_code=status.HTTP_201_CREATED)
async def create_chapter(
    novel_id: int,
    body: ChapterCreate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new chapter in a novel."""
    await _get_owned_novel(novel_id, user, db)

    # Auto-assign word count if content is provided
    word_count = len(body.content.split()) if body.content else 0

    chapter = Chapter(
        novel_id=novel_id,
        title=body.title,
        content=body.content,
        order_index=body.order_index or 0,
        word_count=word_count,
    )
    db.add(chapter)
    await db.flush()
    await db.refresh(chapter)
    return chapter


@router.get("/{chapter_id}", response_model=ChapterOut)
async def get_chapter(
    novel_id: int,
    chapter_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single chapter."""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.novel_id == novel_id)
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    return chapter


@router.put("/{chapter_id}", response_model=ChapterOut)
async def update_chapter(
    novel_id: int,
    chapter_id: int,
    body: ChapterUpdate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a chapter's metadata or content."""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.novel_id == novel_id)
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")

    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(chapter, key, value)

    # Recalculate word count when content changes
    if body.content is not None:
        chapter.word_count = len(body.content.split())

    await db.flush()
    await db.refresh(chapter)
    return chapter


@router.delete("/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chapter(
    novel_id: int,
    chapter_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chapter."""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.novel_id == novel_id)
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    await db.delete(chapter)
    return None


@router.post("/reorder", response_model=List[ChapterOut])
async def reorder_chapters(
    novel_id: int,
    body: ChapterReorder,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch update chapter order_index values."""
    await _get_owned_novel(novel_id, user, db)

    result = await db.execute(
        select(Chapter).where(Chapter.novel_id == novel_id)
    )
    chapters = {ch.id: ch for ch in result.scalars().all()}

    for ch_id, new_order in body.order.items():
        if ch_id in chapters:
            chapters[ch_id].order_index = new_order

    await db.flush()

    # Return updated list in order
    result = await db.execute(
        select(Chapter).where(Chapter.novel_id == novel_id).order_by(Chapter.order_index)
    )
    return result.scalars().all()
