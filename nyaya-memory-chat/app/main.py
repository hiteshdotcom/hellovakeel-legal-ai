"""FastAPI application entrypoint.

Startup wiring (in order):
  1. asyncpg pool + memchat migration (init_db).
  2. configure_cognee (assert 1536 dims, assert separate DB) + pick memory backend.
  3. mount API + static chat UI.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.chat import router as chat_router
from .auth import router as auth_router
from .config import get_settings
from .db import get_db, init_db
from .llm import get_llm
from .memory import user_memory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nyaya.main")

_WEB = Path(__file__).resolve().parents[1] / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting nyaya-memory-chat (env=%s)", settings.ENVIRONMENT)
    db = await init_db()
    backend = user_memory.init_memory(db, get_llm())
    logger.info("DB available=%s · memory backend=%s", db.available, backend.name)
    yield
    await get_db().close()


app = FastAPI(title="nyaya-memory-chat", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth")
app.include_router(chat_router, prefix="/api")


@app.get("/healthz")
async def healthz():
    db = get_db()
    backend = user_memory.get_backend()
    return JSONResponse(
        {
            "status": "ok",
            "db_available": db.available,
            "memory_backend": backend.name,
            "google_oauth": get_settings().google_oauth_enabled,
        }
    )


# ---- static chat UI ----
# Prefer the built React app (frontend/dist) when present; otherwise fall back to
# the legacy single-file web/index.html. Build it with: cd frontend && npm run build
_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
_STATIC_ROOT = _DIST if (_DIST / "index.html").exists() else _WEB

_INDEX_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def _serve_index():
    idx = _STATIC_ROOT / "index.html"
    if idx.exists():
        return FileResponse(str(idx), headers=_INDEX_HEADERS)
    return JSONResponse({"service": "nyaya-memory-chat", "docs": "/docs"})


if _STATIC_ROOT.exists():
    # Vite emits hashed bundles under /assets referenced by absolute path.
    _assets = _STATIC_ROOT / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
    if _WEB.exists():
        app.mount("/web", StaticFiles(directory=str(_WEB)), name="web")

    @app.get("/")
    async def index():
        return _serve_index()

    # Clerk redirects the OAuth (Google) flow back here; the SPA finalizes it.
    @app.get("/sso-callback")
    async def sso_callback():
        return _serve_index()

    # SPA history fallback: serve real root files (favicon, etc.) or index.html.
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith(("api/", "healthz", "assets/", "web/", "docs", "openapi.json")):
            return JSONResponse({"error": "not found"}, status_code=404)
        candidate = _STATIC_ROOT / full_path
        if candidate.is_file() and candidate.resolve().is_relative_to(_STATIC_ROOT.resolve()):
            return FileResponse(str(candidate))
        return _serve_index()

else:  # pragma: no cover

    @app.get("/")
    async def root():
        return JSONResponse({"service": "nyaya-memory-chat", "docs": "/docs"})
