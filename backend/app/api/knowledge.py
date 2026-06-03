"""Knowledge base routes — CRUD for knowledge bases, entries, and relations."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_approved_user
from app.db.session import get_db
from app.models.knowledge import KnowledgeBase, KnowledgeEntry, KnowledgeRelation
from app.models.user import User

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge"])

# ── Schemas ───────────────────────────────────────────

class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    novel_id: int | None = None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    novel_id: int | None = None


class KnowledgeBaseOut(BaseModel):
    id: int
    name: str
    description: str | None
    novel_id: int | None
    created_at: str
    model_config = {"from_attributes": True}


class KnowledgeEntryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str | None = None
    type: str = Field(default="custom")
    metadata_json: str | None = None


class KnowledgeEntryUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = None
    type: str | None = None
    metadata_json: str | None = None


class KnowledgeEntryOut(BaseModel):
    id: int
    knowledge_base_id: int
    title: str
    content: str | None
    type: str
    metadata_json: str | None
    created_at: str
    updated_at: str
    model_config = {"from_attributes": True}


class KnowledgeRelationCreate(BaseModel):
    source_entry_id: int
    target_entry_id: int
    relation_type: str = Field(..., min_length=1, max_length=64)
    description: str | None = None


class KnowledgeRelationOut(BaseModel):
    id: int
    source_entry_id: int
    target_entry_id: int
    relation_type: str
    description: str | None
    model_config = {"from_attributes": True}


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    val: int = 1


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str


class GraphData(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]


# ── Knowledge Bases ────────────────────────────────────

@router.get("", response_model=List[KnowledgeBaseOut])
async def list_knowledge_bases(
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    body: KnowledgeBaseCreate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    kb = KnowledgeBase(**body.model_dump())
    db.add(kb)
    await db.flush()
    await db.refresh(kb)
    return kb


@router.put("/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    kb_id: int,
    body: KnowledgeBaseUpdate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(kb, key, value)
    await db.flush()
    await db.refresh(kb)
    return kb


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    await db.delete(kb)
    return None


# ── Entries ────────────────────────────────────────────

@router.get("/{kb_id}/entries", response_model=List[KnowledgeEntryOut])
async def list_entries(
    kb_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.knowledge_base_id == kb_id)
        .order_by(KnowledgeEntry.updated_at.desc())
    )
    return result.scalars().all()


@router.post("/{kb_id}/entries", response_model=KnowledgeEntryOut, status_code=status.HTTP_201_CREATED)
async def create_entry(
    kb_id: int,
    body: KnowledgeEntryCreate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    entry = KnowledgeEntry(knowledge_base_id=kb_id, **body.model_dump())
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return entry


# ── Entry update/delete at top level ───────────────────

@router.put("/entries/{entry_id}", response_model=KnowledgeEntryOut)
async def update_entry(
    entry_id: int,
    body: KnowledgeEntryUpdate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(entry, key, value)
    await db.flush()
    await db.refresh(entry)
    return entry


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    await db.delete(entry)
    return None


# ── Graph ──────────────────────────────────────────────

@router.get("/{kb_id}/graph", response_model=GraphData)
async def get_graph(
    kb_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    # Load all entries for this KB
    result = await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.knowledge_base_id == kb_id)
    )
    entries = result.scalars().all()

    # Load relations for these entries
    entry_ids = [e.id for e in entries]
    rels = []
    if entry_ids:
        rel_result = await db.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.source_entry_id.in_(entry_ids)
            )
        )
        rels = rel_result.scalars().all()

    nodes = [
        GraphNode(
            id=str(e.id),
            label=e.title,
            type=e.type,
            val=max(1, len(e.content or "") // 100),
        )
        for e in entries
    ]
    edges = [
        GraphEdge(
            source=str(r.source_entry_id),
            target=str(r.target_entry_id),
            label=r.relation_type,
        )
        for r in rels
    ]

    return GraphData(nodes=nodes, edges=edges)


# ── Relations ──────────────────────────────────────────

@router.post("/relations", response_model=KnowledgeRelationOut, status_code=status.HTTP_201_CREATED)
async def create_relation(
    body: KnowledgeRelationCreate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    rel = KnowledgeRelation(**body.model_dump())
    db.add(rel)
    await db.flush()
    await db.refresh(rel)
    return rel


@router.delete("/relations/{rel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_relation(
    rel_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeRelation).where(KnowledgeRelation.id == rel_id))
    rel = result.scalar_one_or_none()
    if not rel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relation not found")
    await db.delete(rel)
    return None
