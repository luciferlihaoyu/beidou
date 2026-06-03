"""Auth-related Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.user import UserRole, UserStatus


class UserRegister(BaseModel):
    """Request body for user registration."""

    username: str = Field(..., min_length=3, max_length=64)
    email: str
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may only contain letters, numbers, _, -")
        return v


class UserLogin(BaseModel):
    """Request body for login."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """JWT token returned after successful login."""

    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    """Public user representation."""

    id: int
    username: str
    email: str
    role: UserRole
    status: UserStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class UserStatusUpdate(BaseModel):
    """Admin action: approve or reject a user."""

    status: UserStatus
