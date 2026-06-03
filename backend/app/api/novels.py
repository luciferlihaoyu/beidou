"""Novel CRUD routes."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_approved_user
from app.db.session import get_db
from app.models.novel import Novel
from app.models.user import User
from app.schemas.content import NovelCreate, NovelOut, NovelUpdate

router = APIRouter(prefix="/api/novels", tags=["novels"])


@router.get("", response_model=List[NovelOut])
async def list_novels(
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """List all novels belonging to the current user."""
    result = await db.execute(
        select(Novel).where(Novel.user_id == user.id).order_by(Novel.updated_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=NovelOut, status_code=status.HTTP_201_CREATED)
async def create_novel(
    body: NovelCreate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new novel for the current user."""
    novel = Novel(**body.model_dump(), user_id=user.id)
    db.add(novel)
    await db.flush()
    await db.refresh(novel)
    return novel


@router.get("/{novel_id}", response_model=NovelOut)
async def get_novel(
    novel_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single novel by ID (owner or admin)."""
    novel = await _get_owned_novel(novel_id, user, db)
    return novel


@router.put("/{novel_id}", response_model=NovelOut)
async def update_novel(
    novel_id: int,
    body: NovelUpdate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a novel's metadata."""
    novel = await _get_owned_novel(novel_id, user, db)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(novel, key, value)
    await db.flush()
    await db.refresh(novel)
    return novel


@router.delete("/{novel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_novel(
    novel_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a novel and all its chapters/settings."""
    novel = await _get_owned_novel(novel_id, user, db)
    await db.delete(novel)
    return None


async def _get_owned_novel(novel_id: int, user: User, db: AsyncSession) -> Novel:
    """Fetch a novel ensuring the requesting user owns it (or is admin)."""
    result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novel not found")
    if novel.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return novel
