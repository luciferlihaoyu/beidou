"""Book API routes"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models import Book

router = APIRouter(prefix="/api/books", tags=["books"])


class BookCreate(BaseModel):
    title: str
    author: str = ""
    description: str = ""
    genre: str = ""
    cover_url: str = ""


class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    cover_url: Optional[str] = None
    status: Optional[str] = None


@router.get("")
def list_books(db: Session = Depends(get_db)):
    books = db.query(Book).order_by(Book.updated_at.desc()).all()
    return [{
        "id": b.id, "title": b.title, "author": b.author,
        "description": b.description, "genre": b.genre,
        "cover_url": b.cover_url, "status": b.status,
        "word_count": b.word_count,
        "chapter_count": len(b.chapters),
        "volume_count": len(b.volumes),
        "created_at": str(b.created_at), "updated_at": str(b.updated_at)
    } for b in books]


@router.post("")
def create_book(data: BookCreate, db: Session = Depends(get_db)):
    book = Book(**data.model_dump())
    db.add(book)
    db.commit()
    db.refresh(book)
    return {"id": book.id, "title": book.title}


@router.get("/{book_id}")
def get_book(book_id: int, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(404, "Book not found")
    return {
        "id": book.id, "title": book.title, "author": book.author,
        "description": book.description, "genre": book.genre,
        "cover_url": book.cover_url, "status": book.status,
        "word_count": book.word_count,
        "chapter_count": len(book.chapters),
        "volume_count": len(book.volumes),
        "volumes": [{
            "id": v.id, "title": v.title,
            "description": v.description,
            "sort_order": v.sort_order,
            "chapter_count": len(v.chapters)
        } for v in book.volumes],
        "chapters": [{
            "id": ch.id, "volume_id": ch.volume_id,
            "title": ch.title, "word_count": ch.word_count,
            "sort_order": ch.sort_order,
            "updated_at": str(ch.updated_at)
        } for ch in book.chapters],
        "created_at": str(book.created_at), "updated_at": str(book.updated_at)
    }


@router.put("/{book_id}")
def update_book(book_id: int, data: BookUpdate, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(404, "Book not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(book, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/{book_id}")
def delete_book(book_id: int, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(404, "Book not found")
    db.delete(book)
    db.commit()
    return {"ok": True}
