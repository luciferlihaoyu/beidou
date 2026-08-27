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
        # 幂等补字段：SQLite 上 SQLAlchemy 不会自动 emit ALTER，所以手工 PRAGMA + ADD COLUMN
        # 仅对存量表生效；新表会在 create_all 阶段拿到新列
        await _ensure_column(conn, "chapters", "status", "TEXT NOT NULL DEFAULT 'draft'")
        await _ensure_column(conn, "chapters", "tags", "TEXT NOT NULL DEFAULT '[]'")

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


async def _ensure_column(conn, table: str, column: str, definition: str) -> None:
    """若表缺少指定列则 ALTER TABLE ADD COLUMN。幂等：每次启动都跑安全。

    SQLite 限制：ADD COLUMN 不支持外键约束（与本章无关）；NOT NULL 必须给 DEFAULT。
    conn 是 AsyncConnection（来自 ``async with engine.begin()``）。
    """
    from sqlalchemy import text

    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    if not any(r[1] == column for r in rows):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


async def _migrate():
    """轻量迁移（SQLite 无 Alembic）：

    1. chapters 表补 volume_id 列（分卷）
    2. 剥离旧章节标题中的"第X章"前缀（序号改为系统按排序生成）
    3. novels 表补 daily_goal 列（每日码字目标）
    4. chapter_snapshots 章节快照/存稿点表（CREATE TABLE IF NOT EXISTS 幂等）
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
        # 章节快照/存稿点表（t4-snapshots）。CREATE TABLE IF NOT EXISTS 自身幂等。
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS chapter_snapshots ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE, "
                "content TEXT NOT NULL, "
                "content_text TEXT NOT NULL, "
                "word_count INTEGER NOT NULL DEFAULT 0, "
                "content_hash VARCHAR(64) NOT NULL, "
                "label VARCHAR(100) NOT NULL DEFAULT '', "
                "trigger VARCHAR(16) NOT NULL, "
                "created_at DATETIME NOT NULL"
                ")"
            )
        )
        # 索引 IF NOT EXISTS 也幂等
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_chapter_snapshots_chapter_id ON chapter_snapshots (chapter_id)")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_chapter_snapshots_content_hash ON chapter_snapshots (content_hash)")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_chapter_snapshots_created_at ON chapter_snapshots (created_at)")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_snap_chapter_created ON chapter_snapshots (chapter_id, created_at)")
        )

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
