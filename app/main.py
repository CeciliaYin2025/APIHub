"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import audit, auth, databases, eo, health, mcp, procedures, queries, schemas, tools, writes
from app.core.config import settings
from app.db.multi_pool import db_registry
from app.db.oracle_pool import close_pool, init_pool
from app.middleware.audit import AuditMiddleware
from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.concurrency_limiter import ConcurrencyLimiterMiddleware
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.services import oauth_rs
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
    await oauth_rs.aclose()
    logger.info("Oracle API Hub shut down")


def _swagger_oauth_config() -> dict | None:
    """Pre-fill Swagger's OAuth dialog, or None to leave the flow out.

    PKCE is forced on: isap-auth registers public clients only
    (token_endpoint_auth_methods_supported = ["none"]), so a Swagger login
    without a verifier has nothing to prove itself with. The redirect URI it
    uses — <APIHUB_PUBLIC_URL>/docs/oauth2-redirect — must be registered in
    ISAP_REDIRECT_URIS or /authorize rejects the request outright.
    """
    if not (settings.oauth_rs_enabled and settings.isap_auth_public_url):
        return None
    return {
        "clientId": settings.isap_app_id,
        "usePkceWithAuthorizationCodeGrant": True,
        "scopes": "openid profile email",
    }


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
    root_path=settings.root_path,
    swagger_ui_init_oauth=_swagger_oauth_config(),
)

# ── Middleware ────────────────────────────────────────────────────────────────
# Order matters. add_middleware stacks last-added = outermost. We want, outer→inner:
#   CORS → Audit → AuthContext → RateLimiter → ConcurrencyLimiter → routers
app.add_middleware(ConcurrencyLimiterMiddleware)  # innermost: holds a slot for the whole call
app.add_middleware(RateLimiterMiddleware)  # cheap check first — reject floods before queueing
app.add_middleware(AuthContextMiddleware)  # resolves principal for both limiters
app.add_middleware(AuditMiddleware)  # times whole chain, sees final principal
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers hide non-safelisted response headers from JS unless exposed, so without
    # this a web client sees the 429 but cannot read how long to wait.
    expose_headers=["Retry-After"],
)

# ── Routers ──────────────────────────────────────────────────────────────────
# Database-independent endpoints
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(databases.router, prefix=settings.api_prefix)
app.include_router(audit.router, prefix=settings.api_prefix)
app.include_router(mcp.router, prefix=settings.api_prefix)
app.include_router(eo.router, prefix=settings.api_prefix)

# These routers contain both global paths (e.g. /queries/definitions)
# and database-scoped paths (e.g. /{db}/queries/execute)
app.include_router(queries.router, prefix=settings.api_prefix)
app.include_router(procedures.router, prefix=settings.api_prefix)
app.include_router(writes.router, prefix=settings.api_prefix)
app.include_router(schemas.router, prefix=settings.api_prefix)
app.include_router(tools.router, prefix=settings.api_prefix)

# ── OAuth 2.1 Resource Server metadata ───────────────────────────────────────


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
def oauth_protected_resource():
    """RFC 9728 metadata: which authorization server issues tokens for this API.

    Served at the app root, so behind the gateway it lands on
    ``<root_path>/.well-known/...``. RFC 9728 clients build the URL by inserting
    the well-known path at the DOMAIN root instead
    (``/.well-known/oauth-protected-resource/apihub``), which only a gateway
    rewrite can route here — see
    deploy/apihub/gateway-conf/oauth-wellknown-apihub.snippet.conf.
    """
    if not oauth_rs.enabled():
        raise HTTPException(status_code=404, detail="Not found")
    return oauth_rs.protected_resource_metadata()


# ── Admin UI (static SPA) ────────────────────────────────────────────────────
app.mount("/admin/css", StaticFiles(directory=str(_ADMIN_DIR / "css")), name="admin-css")
app.mount("/admin/js", StaticFiles(directory=str(_ADMIN_DIR / "js")), name="admin-js")


@app.get("/admin")
@app.get("/admin/{rest_of_path:path}")
def admin_spa(rest_of_path: str = ""):
    """Serve the admin SPA index.html, substituting the base path prefix."""
    html = (_ADMIN_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{BASE_PATH}}", settings.root_path)
    # Lower-cased so it is a valid JS boolean literal, not Python's True/False.
    html = html.replace("{{SSO_ENABLED}}", "true" if settings.admin_sso_enabled else "false")
    return HTMLResponse(content=html)
