"""Application settings model (key-value store)"""
from sqlalchemy import Column, Integer, String
from database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(String(500), default="")
