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

    await _migrate()

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


async def _migrate():
    """轻量迁移（SQLite 无 Alembic）：

    1. chapters 表补 volume_id 列（分卷）
    2. 剥离旧章节标题中的"第X章"前缀（序号改为系统按排序生成）
    3. novels 表补 daily_goal 列（每日码字目标）
    以上操作都是幂等的，每次启动执行。
    """
    from sqlalchemy import text

    from .utils import strip_chapter_prefix

    async with engine.begin() as conn:
        cols = (await conn.execute(text("PRAGMA table_info(chapters)"))).fetchall()
        if cols and not any(c[1] == "volume_id" for c in cols):
            await conn.execute(text("ALTER TABLE chapters ADD COLUMN volume_id INTEGER REFERENCES volumes(id) ON DELETE SET NULL"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_chapters_volume_id ON chapters (volume_id)"))
        novel_cols = (await conn.execute(text("PRAGMA table_info(novels)"))).fetchall()
        if novel_cols and not any(c[1] == "daily_goal" for c in novel_cols):
            await conn.execute(text("ALTER TABLE novels ADD COLUMN daily_goal INTEGER NOT NULL DEFAULT 0"))

    from sqlalchemy import select

    from .models import Chapter

    async with SessionLocal() as session:
        chapters = (await session.execute(select(Chapter))).scalars().all()
        changed = False
        for c in chapters:
            stripped = strip_chapter_prefix(c.title)
            if stripped != c.title.strip():
                c.title = stripped
                changed = True
        if changed:
            await session.commit()
