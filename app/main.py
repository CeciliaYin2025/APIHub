"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, health, procedures, queries, writes
from app.core.config import settings
from app.db.oracle_pool import close_pool, init_pool
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.services.sql_registry import sql_registry

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting Oracle API Hub v%s", settings.api_version)
    init_pool()
    sql_registry.load()
    yield
    close_pool()
    logger.info("Oracle API Hub shut down")


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
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
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(queries.router, prefix=settings.api_prefix)
app.include_router(procedures.router, prefix=settings.api_prefix)
app.include_router(writes.router, prefix=settings.api_prefix)
