from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import ai, auth, chapters, export, integrations, library, novels, skills, volumes, settings as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="北斗 · AI 网文创作平台", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(novels.router)
app.include_router(volumes.router)
app.include_router(chapters.router)
app.include_router(settings_router.router)
app.include_router(ai.router)
app.include_router(skills.router)
app.include_router(library.router)
app.include_router(integrations.router)
app.include_router(export.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "name": "beidou"}


# 静态托管前端构建产物（SPA 回退）
static_dir = Path(settings.static_dir) if settings.static_dir else None
if static_dir and (static_dir / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        target = static_dir / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(static_dir / "index.html")
