"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import auth, databases, health, procedures, queries, schemas, writes
from app.core.config import settings
from app.db.multi_pool import db_registry
from app.db.oracle_pool import close_pool, init_pool
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.services.sql_registry import sql_registry

_ADMIN_DIR = Path(__file__).resolve().parent / "admin"

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting Oracle API Hub v%s", settings.api_version)
    db_registry.load()
    init_pool()
    sql_registry.load()
    yield
    close_pool()
    logger.info("Oracle API Hub shut down")


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
    root_path=settings.root_path,
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimiterMiddleware)

# ── Routers ──────────────────────────────────────────────────────────────────
# Database-independent endpoints
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(databases.router, prefix=settings.api_prefix)

# These routers contain both global paths (e.g. /queries/definitions)
# and database-scoped paths (e.g. /{db}/queries/execute)
app.include_router(queries.router, prefix=settings.api_prefix)
app.include_router(procedures.router, prefix=settings.api_prefix)
app.include_router(writes.router, prefix=settings.api_prefix)
app.include_router(schemas.router, prefix=settings.api_prefix)

# ── Admin UI (static SPA) ────────────────────────────────────────────────────
app.mount("/admin/css", StaticFiles(directory=str(_ADMIN_DIR / "css")), name="admin-css")
app.mount("/admin/js", StaticFiles(directory=str(_ADMIN_DIR / "js")), name="admin-js")


@app.get("/admin")
@app.get("/admin/{rest_of_path:path}")
def admin_spa(rest_of_path: str = ""):
    """Serve the admin SPA index.html, substituting the base path prefix."""
    html = (_ADMIN_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{BASE_PATH}}", settings.root_path)
    return HTMLResponse(content=html)
