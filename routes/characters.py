"""Character & World Setting API routes"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models import Character, WorldSetting

router = APIRouter(tags=["characters-world"])


class CharacterCreate(BaseModel):
    name: str
    gender: str = ""
    age: str = ""
    role: str = ""
    personality: str = ""
    background: str = ""
    experiences: str = ""
    appearance: str = ""
    abilities: str = ""
    motivation: str = ""
    relationships: str = ""
    weaknesses: str = ""
    speech_pattern: str = ""
    notes: str = ""
    avatar_url: str = ""


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[str] = None
    role: Optional[str] = None
    personality: Optional[str] = None
    background: Optional[str] = None
    experiences: Optional[str] = None
    appearance: Optional[str] = None
    abilities: Optional[str] = None
    motivation: Optional[str] = None
    relationships: Optional[str] = None
    weaknesses: Optional[str] = None
    speech_pattern: Optional[str] = None
    notes: Optional[str] = None
    avatar_url: Optional[str] = None


@router.get("/api/books/{book_id}/characters")
def list_characters(book_id: int, db: Session = Depends(get_db)):
    chars = db.query(Character).filter(Character.book_id == book_id).all()
    return [char_to_dict(c) for c in chars]


@router.post("/api/books/{book_id}/characters")
def create_character(book_id: int, data: CharacterCreate, db: Session = Depends(get_db)):
    ch = Character(book_id=book_id, **data.model_dump())
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return {"id": ch.id, "name": ch.name}


@router.put("/api/characters/{char_id}")
def update_character(char_id: int, data: CharacterUpdate, db: Session = Depends(get_db)):
    ch = db.query(Character).filter(Character.id == char_id).first()
    if not ch:
        raise HTTPException(404)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(ch, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/api/characters/{char_id}")
def delete_character(char_id: int, db: Session = Depends(get_db)):
    ch = db.query(Character).filter(Character.id == char_id).first()
    if not ch:
        raise HTTPException(404)
    db.delete(ch)
    db.commit()
    return {"ok": True}


# ─── World Settings ───

@router.get("/api/books/{book_id}/settings")
def list_settings(book_id: int, db: Session = Depends(get_db)):
    items = db.query(WorldSetting).filter(WorldSetting.book_id == book_id).all()
    return [{"id": s.id, "category": s.category, "title": s.title, "content": s.content} for s in items]


@router.post("/api/books/{book_id}/settings")
def create_setting(book_id: int, data: dict, db: Session = Depends(get_db)):
    s = WorldSetting(book_id=book_id, category=data.get("category", ""),
                     title=data.get("title", ""), content=data.get("content", ""))
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id}


@router.put("/api/settings/{setting_id}")
def update_setting(setting_id: int, data: dict, db: Session = Depends(get_db)):
    s = db.query(WorldSetting).filter(WorldSetting.id == setting_id).first()
    if not s:
        raise HTTPException(404)
    if "title" in data: s.title = data["title"]
    if "content" in data: s.content = data["content"]
    db.commit()
    return {"ok": True}


@router.delete("/api/settings/{setting_id}")
def delete_setting(setting_id: int, db: Session = Depends(get_db)):
    s = db.query(WorldSetting).filter(WorldSetting.id == setting_id).first()
    if not s:
        raise HTTPException(404)
    db.delete(s)
    db.commit()
    return {"ok": True}


def char_to_dict(c: Character) -> dict:
    return {
        "id": c.id, "book_id": c.book_id, "name": c.name,
        "gender": c.gender, "age": c.age, "role": c.role,
        "personality": c.personality, "background": c.background,
        "experiences": c.experiences, "appearance": c.appearance,
        "abilities": c.abilities, "motivation": c.motivation,
        "relationships": c.relationships, "weaknesses": c.weaknesses,
        "speech_pattern": c.speech_pattern, "notes": c.notes,
        "avatar_url": c.avatar_url
    }
