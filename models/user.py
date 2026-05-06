"""User and AuthToken models"""
import hashlib
import os
from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, DateTime, func
from database import Base


def _hash_password(password):
    salt = os.urandom(16).hex()
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(stored_hash, password):
    salt, h = stored_hash.split("$", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def _gen_token():
    return os.urandom(32).hex()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(20), nullable=False, default="user")
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, server_default=func.now())

    def set_password(self, password):
        self.password_hash = _hash_password(password)

    def check_password(self, password):
        return _verify_password(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "status": self.status,
            "created_at": str(self.created_at),
        }


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)

    @staticmethod
    def generate(user_id):
        return AuthToken(
            user_id=user_id,
            token=_gen_token(),
            expires_at=datetime.utcnow() + timedelta(days=7),
        )

    def is_expired(self):
        return datetime.utcnow() > self.expires_at
