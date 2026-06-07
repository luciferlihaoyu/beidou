"""Auth routes: register, login, get current user."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.db.session import get_db
from app.models.user import User, UserRole, UserStatus
from app.schemas.auth import TokenResponse, UserLogin, UserOut, UserRegister

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user (status defaults to 'pending')."""
    # Check for existing username
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    # Check for existing email
    existing_email = await db.execute(select(User).where(User.email == body.email))
    if existing_email.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        username=body.username,
        email=body.email,
        password_hash=get_password_hash(body.password),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    """Authenticate and return JWT token pair."""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if user.status == UserStatus.rejected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been rejected",
        )

    token = create_access_token(data={"sub": user.id})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return current_user


@router.post("/init-admin")
async def init_admin(db: AsyncSession = Depends(get_db)):
    """Emergency: create default admin if no admin user exists. Safe to call multiple times."""
    from app.core.config import get_settings
    settings = get_settings()

    # Check if any admin exists
    result = await db.execute(select(User).where(User.role == UserRole.admin))
    if result.scalar_one_or_none():
        return {"status": "ok", "message": "Admin already exists"}

    # Check if default username is taken by non-admin
    existing = await db.execute(select(User).where(User.username == settings.DEFAULT_ADMIN_USERNAME))
    user = existing.scalar_one_or_none()
    if user:
        # Promote existing user to admin
        user.role = UserRole.admin
        user.status = UserStatus.approved
        await db.flush()
        return {"status": "ok", "message": f'User "{settings.DEFAULT_ADMIN_USERNAME}" promoted to admin'}

    # Create fresh admin
    admin = User(
        username=settings.DEFAULT_ADMIN_USERNAME,
        email=settings.DEFAULT_ADMIN_EMAIL,
        password_hash=get_password_hash(settings.DEFAULT_ADMIN_PASSWORD),
        role=UserRole.admin,
        status=UserStatus.approved,
    )
    db.add(admin)
    await db.flush()
    await db.commit()
    return {"status": "ok", "message": f'Admin user "{settings.DEFAULT_ADMIN_USERNAME}" created'}
