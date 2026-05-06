"""Volume & Chapter API routes"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models import Book, Volume, Chapter

router = APIRouter(tags=["volumes-chapters"])


class VolumeCreate(BaseModel):
    title: str
    description: str = ""


class ChapterCreate(BaseModel):
    title: str
    content: str = ""
    volume_id: Optional[int] = None


class ChapterUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    volume_id: Optional[int] = None


# ─── Volumes ───

@router.get("/api/books/{book_id}/volumes")
def list_volumes(book_id: int, db: Session = Depends(get_db)):
    vols = db.query(Volume).filter(Volume.book_id == book_id).order_by(Volume.sort_order).all()
    return [{"id": v.id, "title": v.title, "description": v.description,
             "sort_order": v.sort_order, "chapter_count": len(v.chapters)} for v in vols]


@router.post("/api/books/{book_id}/volumes")
def create_volume(book_id: int, data: VolumeCreate, db: Session = Depends(get_db)):
    vol = Volume(book_id=book_id, title=data.title, description=data.description,
                 sort_order=len(db.query(Volume).filter(Volume.book_id == book_id).all()))
    db.add(vol)
    db.commit()
    db.refresh(vol)
    return {"id": vol.id, "title": vol.title}


@router.put("/api/volumes/{volume_id}")
def update_volume(volume_id: int, data: VolumeCreate, db: Session = Depends(get_db)):
    vol = db.query(Volume).filter(Volume.id == volume_id).first()
    if not vol:
        raise HTTPException(404)
    vol.title = data.title
    vol.description = data.description
    db.commit()
    return {"ok": True}


@router.delete("/api/volumes/{volume_id}")
def delete_volume(volume_id: int, db: Session = Depends(get_db)):
    vol = db.query(Volume).filter(Volume.id == volume_id).first()
    if not vol:
        raise HTTPException(404)
    db.delete(vol)
    db.commit()
    return {"ok": True}


# ─── Chapters ───

@router.get("/api/books/{book_id}/chapters")
def list_chapters(book_id: int, db: Session = Depends(get_db)):
    chs = db.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.sort_order).all()
    return [{"id": ch.id, "volume_id": ch.volume_id, "title": ch.title,
             "word_count": ch.word_count, "sort_order": ch.sort_order,
             "updated_at": str(ch.updated_at)} for ch in chs]


@router.post("/api/books/{book_id}/chapters")
def create_chapter(book_id: int, data: ChapterCreate, db: Session = Depends(get_db)):
    count = db.query(Chapter).filter(Chapter.book_id == book_id).count()
    title = data.title or f"第{count + 1}章"
    ch = Chapter(book_id=book_id, volume_id=data.volume_id, title=title,
                 content=data.content, sort_order=count)
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return {"id": ch.id, "title": ch.title}


@router.get("/api/chapters/{chapter_id}")
def get_chapter(chapter_id: int, db: Session = Depends(get_db)):
    ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not ch:
        raise HTTPException(404)
    return {"id": ch.id, "book_id": ch.book_id, "volume_id": ch.volume_id,
            "title": ch.title, "content": ch.content,
            "word_count": ch.word_count, "sort_order": ch.sort_order,
            "updated_at": str(ch.updated_at)}


@router.put("/api/chapters/{chapter_id}")
def update_chapter(chapter_id: int, data: ChapterUpdate, db: Session = Depends(get_db)):
    ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not ch:
        raise HTTPException(404)
    if data.title is not None:
        ch.title = data.title
    if data.content is not None:
        ch.content = data.content
        ch.word_count = len(data.content.replace(" ", "").replace("\n", ""))
    if data.volume_id is not None:
        ch.volume_id = data.volume_id
    db.commit()
    return {"ok": True}


@router.delete("/api/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, db: Session = Depends(get_db)):
    ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not ch:
        raise HTTPException(404)
    db.delete(ch)
    db.commit()
    return {"ok": True}
