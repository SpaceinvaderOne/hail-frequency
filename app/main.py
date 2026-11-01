import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import AuthMiddleware
from .database import init_db
from .routers import auth as auth_router
from .routers import browse as browse_router
from .routers import devices as devices_router
from .routers import discovery as discovery_router
from .routers import history as history_router
from .routers import playback as playback_router
from .routers import settings as settings_router
from .routers import setup as setup_router
from .routers import users as users_router
from .routers import voice as voice_router

logger = logging.getLogger("hailfrequency")

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Owner-only for everything this process creates (the SQLite database
    # holds media-server API keys and access tokens in plaintext)
    os.umask(0o077)
    from .auth import maybe_reset_admin_password
    from .logging_config import restore_debug_logging, setup_stderr_logging
    setup_stderr_logging()
    logger.info("Initializing database...")
    init_db()
    maybe_reset_admin_password()
    restore_debug_logging()
    logger.info("HailFrequency started.")
    yield
    from .services.media_http import close_all
    await close_all()
    logger.info("HailFrequency shutting down.")


app = FastAPI(title="HailFrequency", version="1.0.0", lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth headers; frontend is already textContent-disciplined."""

    CSP = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        "media-src 'self'; "
        "connect-src 'self'"
    )

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", self.CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # App shell + statics: no-cache = store but ALWAYS revalidate.
        # Without this, browsers apply heuristic freshness to Last-Modified
        # and serve stale JS/CSS without ever asking the server — the SW's
        # stale-while-revalidate then "refreshes" from that stale HTTP cache
        # and updates never reach clients. Revalidation is a cheap 304.
        ctype = response.headers.get("content-type", "")
        if request.url.path.startswith("/static") or ctype.startswith("text/html"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


# Middleware (outermost first: security headers wrap everything)
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Routers
app.include_router(auth_router.router)
app.include_router(setup_router.router)
app.include_router(discovery_router.router)
app.include_router(voice_router.router)
app.include_router(playback_router.router)
app.include_router(browse_router.router)
app.include_router(history_router.router)
app.include_router(devices_router.router)
app.include_router(settings_router.router)
app.include_router(users_router.router)

# Static files — mounted after routers so API routes take priority
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
async def health():
    from .version import version_info
    return {"status": "ok", **version_info()}


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # Unknown API routes must 404, not silently return the SPA shell
    if full_path.startswith("api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(STATIC_DIR / "index.html")
