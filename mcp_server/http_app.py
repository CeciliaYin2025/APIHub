"""Streamable HTTP transport for the MCP server.

Phase 1 auth = credential passthrough: the caller's ``X-API-Key`` (or
``Authorization: Bearer <api key>``) header is forwarded on every proxied
REST call, so the API Hub validates the credential and applies RBAC, audit,
and rate limiting per caller. The MCP server itself validates nothing.

The session manager runs in stateless mode on purpose: each HTTP request
executes tool handlers inside its own task context, which is what makes the
contextvar-based header passthrough correct. Do not switch to stateful mode
without replacing the passthrough with per-session credential binding
(Phase 2 OAuth does exactly that).
"""

import contextlib
import contextvars

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient
from mcp_server.config import mcp_settings
from mcp_server.server import create_server

_request_headers: contextvars.ContextVar[dict] = contextvars.ContextVar("mcp_request_headers", default={})


def passthrough_headers() -> dict:
    """Header provider: whatever credential the current HTTP request carried."""
    return _request_headers.get()


class CredentialPassthroughMiddleware:
    """Copy the caller's API credential header into a contextvar for ApiHubClient."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        cred: dict = {}
        if headers.get("x-api-key"):
            cred = {"X-API-Key": headers["x-api-key"]}
        elif headers.get("authorization"):
            cred = {"Authorization": headers["authorization"]}
        token = _request_headers.set(cred)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_headers.reset(token)


def create_http_app():
    client = ApiHubClient(mcp_settings.api_hub_url, headers_provider=passthrough_headers)
    aliases = [a.strip() for a in mcp_settings.db_aliases.split(",") if a.strip()] or None
    catalog = ToolCatalog(
        client,
        db_aliases=aliases,
        page_size_max=mcp_settings.page_size_max,
        ttl_seconds=mcp_settings.catalog_ttl_seconds,
    )
    server = create_server(client, catalog)
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    async def handle_mcp(scope, receive, send):
        await session_manager.handle_request(scope, receive, send)

    async def health(request):
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield
        await client.aclose()

    starlette_app = Starlette(
        routes=[Route("/health", health), Mount("/mcp", app=handle_mcp)],
        lifespan=lifespan,
    )
    return CredentialPassthroughMiddleware(starlette_app)


app = create_http_app()
