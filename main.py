"""墨韵小说写作器 - FastAPI 主入口"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from database import init_db
from routes import books, chapters, characters, ai, upload

app = FastAPI(title="墨韵小说写作器", version="2.0.0")

# Include API routes
app.include_router(books.router)
app.include_router(chapters.router)
app.include_router(characters.router)
app.include_router(ai.router)
app.include_router(upload.router)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


# Serve SPA - static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str = ""):
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "墨韵小说写作器 API", "docs": "/docs"}
