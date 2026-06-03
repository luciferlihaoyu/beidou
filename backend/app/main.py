"""FastAPI application factory and lifecycle."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, agents, ai, auth, backup, chapters, database, export, knowledge, models_config, novels, settings
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine
from app.services.startup import ensure_default_admin

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: create tables on startup, cleanup on shutdown."""
    logger.info("Starting up — creating database tables…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Ensure the default admin account exists
    async with engine.begin() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.db.session import async_session_factory
        session = async_session_factory(bind=conn)
        await ensure_default_admin(session)
        await session.close()

    logger.info("Startup complete.")
    yield
    await engine.dispose()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(o) for o in settings.CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    app.include_router(auth.router)
    app.include_router(novels.router)
    app.include_router(chapters.router)
    app.include_router(settings.router)
    app.include_router(admin.router)
    app.include_router(ai.router)
    app.include_router(agents.router)
    app.include_router(models_config.router)
    app.include_router(knowledge.router)
    app.include_router(database.router)
    app.include_router(export.router)
    app.include_router(backup.router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": settings.APP_VERSION}

    # ── 静态文件 & SPA 回退 ──
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists() and (static_dir / "index.html").exists():
        # 挂载静态资源
        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="static-assets")

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """SPA 回退：非 API 路径一律返回 index.html"""
            if full_path.startswith("api/"):
                return None
            file_path = static_dir / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(static_dir / "index.html"))

    return app


# Application instance (used by uvicorn)
app = create_app()
