"""Admin routes: user management."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin_user
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.auth import UserOut, UserStatusUpdate

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


@router.get("", response_model=List[UserOut])
async def list_users(
    user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all registered users (admin only)."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


@router.put("/{user_id}/status", response_model=UserOut)
async def update_user_status(
    user_id: int,
    body: UserStatusUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a user account (admin only)."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own status",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.status = body.status
    await db.flush()
    await db.refresh(target)
    return target


class UserRoleUpdate(BaseModel):
    """Admin action: change user role."""
    role: UserRole


@router.put("/{user_id}/role", response_model=UserOut)
async def update_user_role(
    user_id: int,
    body: UserRoleUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Change a user's role (admin only)."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    target.role = body.role
    await db.flush()
    await db.refresh(target)
    return target
