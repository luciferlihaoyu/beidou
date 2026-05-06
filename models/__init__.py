from .user import User, AuthToken
from .settings import AppSetting

"""SQLAlchemy ORM models"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database import Base

class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    author = Column(String(100), default="")
    description = Column(Text, default="")
    cover_url = Column(String(500), default="")
    genre = Column(String(100), default="")
    status = Column(String(20), default="连载中")  # 连载中/已完结/停更
    word_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    volumes = relationship("Volume", back_populates="book", cascade="all, delete-orphan",
                           order_by="Volume.sort_order")
    chapters = relationship("Chapter", back_populates="book", cascade="all, delete-orphan",
                            order_by="Chapter.sort_order")
    characters = relationship("Character", back_populates="book", cascade="all, delete-orphan")


class Volume(Base):
    __tablename__ = "volumes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    sort_order = Column(Integer, default=0)

    book = relationship("Book", back_populates="volumes")
    chapters = relationship("Chapter", back_populates="volume",
                            order_by="Chapter.sort_order")


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    volume_id = Column(Integer, ForeignKey("volumes.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(300), nullable=False)
    content = Column(Text, default="")
    word_count = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    book = relationship("Book", back_populates="chapters")
    volume = relationship("Volume", back_populates="chapters")


class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    gender = Column(String(20), default="")
    age = Column(String(50), default="")
    role = Column(String(200), default="")
    personality = Column(Text, default="")
    background = Column(Text, default="")
    experiences = Column(Text, default="")
    appearance = Column(Text, default="")
    abilities = Column(Text, default="")
    motivation = Column(Text, default="")
    relationships = Column(Text, default="")
    weaknesses = Column(Text, default="")
    speech_pattern = Column(Text, default="")
    notes = Column(Text, default="")
    avatar_url = Column(String(500), default="")

    book = relationship("Book", back_populates="characters")


class WorldSetting(Base):
    __tablename__ = "world_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(100), nullable=False)  # 时代背景/地理环境/社会结构 etc.
    title = Column(String(200), default="")
    content = Column(Text, default="")
