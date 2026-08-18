from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.db_url, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    from . import models  # noqa: F401  确保模型已注册
    from .security import hash_password

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 首次启动创建默认管理员
    from sqlalchemy import select

    async with SessionLocal() as session:
        result = await session.execute(select(models.User).limit(1))
        if result.scalar_one_or_none() is None:
            session.add(
                models.User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                )
            )
            await session.commit()
