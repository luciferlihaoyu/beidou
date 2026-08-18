"""设定系统：角色 / 世界观 / 伏笔 / 大纲树。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_owned_novel
from ..models import Character, Foreshadowing, Novel, OutlineNode, WorldviewEntry

router = APIRouter(prefix="/api/novels/{novel_id}/settings", tags=["settings"])


# ---------- 角色 ----------

class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default="配角", max_length=50)
    tags: str = ""
    description: str = ""
    relations: str = ""


class CharacterOut(CharacterIn):
    id: int

    model_config = {"from_attributes": True}


@router.get("/characters", response_model=list[CharacterOut])
async def list_characters(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Character).where(Character.novel_id == novel.id).order_by(Character.id)
    )
    return result.scalars().all()


@router.post("/characters", response_model=CharacterOut)
async def create_character(
    data: CharacterIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = Character(novel_id=novel.id, **data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/characters/{item_id}", response_model=CharacterOut)
async def update_character(
    item_id: int, data: CharacterIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = await db.get(Character, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "角色不存在")
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/characters/{item_id}")
async def delete_character(item_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    item = await db.get(Character, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "角色不存在")
    await db.delete(item)
    await db.commit()
    return {"ok": True}


# ---------- 世界观 ----------

class WorldviewIn(BaseModel):
    category: str = Field(default="其他", max_length=50)
    title: str = Field(min_length=1, max_length=200)
    content: str = ""


class WorldviewOut(WorldviewIn):
    id: int

    model_config = {"from_attributes": True}


@router.get("/worldview", response_model=list[WorldviewOut])
async def list_worldview(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WorldviewEntry).where(WorldviewEntry.novel_id == novel.id).order_by(WorldviewEntry.id)
    )
    return result.scalars().all()


@router.post("/worldview", response_model=WorldviewOut)
async def create_worldview(
    data: WorldviewIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = WorldviewEntry(novel_id=novel.id, **data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/worldview/{item_id}", response_model=WorldviewOut)
async def update_worldview(
    item_id: int, data: WorldviewIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = await db.get(WorldviewEntry, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "条目不存在")
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/worldview/{item_id}")
async def delete_worldview(item_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    item = await db.get(WorldviewEntry, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "条目不存在")
    await db.delete(item)
    await db.commit()
    return {"ok": True}


# ---------- 伏笔 ----------

class ForeshadowingIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    status: str = Field(default="未回收", max_length=20)


class ForeshadowingOut(ForeshadowingIn):
    id: int

    model_config = {"from_attributes": True}


@router.get("/foreshadowings", response_model=list[ForeshadowingOut])
async def list_foreshadowings(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Foreshadowing).where(Foreshadowing.novel_id == novel.id).order_by(Foreshadowing.id)
    )
    return result.scalars().all()


@router.post("/foreshadowings", response_model=ForeshadowingOut)
async def create_foreshadowing(
    data: ForeshadowingIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = Foreshadowing(novel_id=novel.id, **data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/foreshadowings/{item_id}", response_model=ForeshadowingOut)
async def update_foreshadowing(
    item_id: int, data: ForeshadowingIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = await db.get(Foreshadowing, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "伏笔不存在")
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/foreshadowings/{item_id}")
async def delete_foreshadowing(
    item_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = await db.get(Foreshadowing, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "伏笔不存在")
    await db.delete(item)
    await db.commit()
    return {"ok": True}


# ---------- 大纲（树形） ----------

class OutlineIn(BaseModel):
    parent_id: int | None = None
    title: str = Field(default="", max_length=200)
    content: str = ""
    sort_order: int = 0


class OutlineOut(OutlineIn):
    id: int

    model_config = {"from_attributes": True}


@router.get("/outline", response_model=list[OutlineOut])
async def list_outline(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(OutlineNode).where(OutlineNode.novel_id == novel.id).order_by(OutlineNode.sort_order, OutlineNode.id)
    )
    return result.scalars().all()


@router.post("/outline", response_model=OutlineOut)
async def create_outline_node(
    data: OutlineIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    if data.parent_id is not None:
        parent = await db.get(OutlineNode, data.parent_id)
        if parent is None or parent.novel_id != novel.id:
            raise HTTPException(400, "父节点不存在")
    item = OutlineNode(novel_id=novel.id, **data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/outline/{item_id}", response_model=OutlineOut)
async def update_outline_node(
    item_id: int, data: OutlineIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = await db.get(OutlineNode, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "节点不存在")
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/outline/{item_id}")
async def delete_outline_node(
    item_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    item = await db.get(OutlineNode, item_id)
    if item is None or item.novel_id != novel.id:
        raise HTTPException(404, "节点不存在")
    await db.delete(item)
    await db.commit()
    return {"ok": True}
