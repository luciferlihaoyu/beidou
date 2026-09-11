from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import engine, init_db
from .routers import ai, ai_extras, ai_factory, ai_import, auth, batch, chapters, export, integrations, library, novels, pomodoro, recycle, relations, search, sso, skills, snapshots, stats, volumes, settings as settings_router


async def _auto_backup_loop():
    """每 30 分钟检查一次：给开启自动备份且今日未备份的用户备份到 AList。"""
    import asyncio

    from .routers.integrations import auto_backup_tick

    while True:
        try:
            await auto_backup_tick()
        except Exception:  # noqa: BLE001  后台任务绝不炸掉主进程
            pass
        await asyncio.sleep(30 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    await init_db()
    backup_task = asyncio.create_task(_auto_backup_loop())
    yield
    backup_task.cancel()


app = FastAPI(title="北斗 · AI 网文创作平台", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局安全响应头：禁用 Referrer 外泄（防 SSO 票等敏感参数经 Referer 泄漏）
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

app.include_router(auth.router)
app.include_router(sso.router)
app.include_router(novels.router)
app.include_router(volumes.router)
app.include_router(chapters.router)
app.include_router(settings_router.router)
app.include_router(ai.router)
app.include_router(pomodoro.router)
app.include_router(recycle.router)
app.include_router(skills.router)
app.include_router(library.router)
app.include_router(integrations.router)
app.include_router(export.router)
app.include_router(stats.router)
app.include_router(search.router)
app.include_router(snapshots.router)
app.include_router(relations.router)
app.include_router(ai_factory.router)
app.include_router(batch.router)
app.include_router(ai_import.router)
app.include_router(ai_extras.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "name": "beidou"}


@app.get("/health")
async def unified_health():
    """统一健康端点（P0-3）：与璇玑/天宫一致，GET /health → {ok, name, db}。

    探活方式：对 SQLite 执行 ``SELECT 1``；失败则 db=False。保留上方
    ``/api/health`` 仅做向后兼容，不再扩展。
    """
    from sqlalchemy import text

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "name": "beidou", "db": db_ok}


# 静态托管前端构建产物（SPA 回退）
static_dir = Path(settings.static_dir) if settings.static_dir else None
if static_dir and (static_dir / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        """SPA 回退：命中静态目录内的真实文件则返回之，否则回退 index.html。

        安全：``full_path`` 来自 URL 路径段（Starlette 已做 URL 解码，
        ``%2e%2e%2f`` 会变成 ``../``）。先 ``resolve()`` 归一化目标路径，
        再用 ``is_relative_to`` 校验其仍位于静态目录之内；不满足（含符号
        链接逃逸、绝对路径注入等）一律回退 index.html，杜绝读取静态
        目录之外的任意文件。
        """
        root = static_dir.resolve()
        target = (static_dir / full_path).resolve()
        if full_path and target.is_relative_to(root) and target.is_file():
            return FileResponse(target)
        return FileResponse(static_dir / "index.html")
