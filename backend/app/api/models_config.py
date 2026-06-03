"""Model configuration management routes."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin_user
from app.db.session import get_db
from app.models.model_config import ModelConfig
from app.models.user import User

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelConfigCreate(BaseModel):
    provider_name: str = Field(..., min_length=1, max_length=128)
    api_base: str | None = None
    api_key: str | None = None
    model_id: str = Field(..., min_length=1, max_length=128)
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    provider_name: str | None = Field(None, min_length=1, max_length=128)
    api_base: str | None = None
    api_key: str | None = None
    model_id: str | None = Field(None, min_length=1, max_length=128)
    is_default: bool | None = None


class ModelConfigOut(BaseModel):
    id: int
    provider_name: str
    api_base: str | None
    api_key: str | None
    model_id: str
    is_default: bool
    created_at: str

    model_config = {"from_attributes": True}


@router.get("", response_model=List[ModelConfigOut])
async def list_models(
    user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ModelConfig).order_by(ModelConfig.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=ModelConfigOut, status_code=status.HTTP_201_CREATED)
async def create_model(
    body: ModelConfigCreate,
    user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = ModelConfig(**body.model_dump())
    db.add(cfg)
    await db.flush()
    await db.refresh(cfg)
    return cfg


@router.put("/{model_id}", response_model=ModelConfigOut)
async def update_model(
    model_id: int,
    body: ModelConfigUpdate,
    user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model config not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(cfg, key, value)
    await db.flush()
    await db.refresh(cfg)
    return cfg


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: int,
    user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model config not found")
    await db.delete(cfg)
    return None


@router.post("/{model_id}/test", response_model=dict)
async def test_model(
    model_id: int,
    user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model config not found")
    return {"success": True, "message": f"Model '{cfg.model_id}' on {cfg.provider_name} is reachable"}


@router.put("/{model_id}/default", response_model=ModelConfigOut)
async def set_default_model(
    model_id: int,
    user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model config not found")
    # Unset all other defaults
    all_cfgs = await db.execute(select(ModelConfig))
    for other in all_cfgs.scalars().all():
        other.is_default = False
    cfg.is_default = True
    await db.flush()
    await db.refresh(cfg)
    return cfg
