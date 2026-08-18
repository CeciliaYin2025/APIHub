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
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient, ApiHubError
from mcp_server.config import mcp_settings
from mcp_server.oauth import TokenExchanger, challenge_header, protected_resource_metadata
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


def _mounted_path(scope) -> str:
    """The request path as this app sees it, with any ASGI root_path removed.

    uvicorn builds ``scope["path"]`` as ``root_path + path`` (see its
    ``httptools_impl``), so when the service runs behind the gateway with
    ``--root-path /apihub/mcp`` a request for ``/mcp/`` arrives as
    ``/apihub/mcp/mcp/``. Matching the raw path against ``/mcp`` therefore never
    fires in deployment — which silently disabled the OAuth gate entirely, since
    it is the only middleware installed when OAuth is on. Strip the prefix first
    so the comparison is about the route, not about where it is mounted.
    """
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root) :] or "/"
    return path


class OAuthGateMiddleware:
    """OAuth 2.1 Resource Server gate for /mcp (Phase 2).

    Validates the caller's Bearer token via isap-auth introspection and puts
    the credential to forward into the passthrough contextvar, so every proxied
    REST call runs with the SSO user's own RBAC and audit identity. What gets
    forwarded — the caller's own token, or a local JWT from the identity
    bridge — is TokenExchanger's decision.
    """

    def __init__(self, app, exchanger: TokenExchanger):
        self.app = app
        self.exchanger = exchanger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _mounted_path(scope).startswith("/mcp"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        credential = None
        if auth.startswith("Bearer "):
            credential = await self.exchanger.resolve(auth[len("Bearer ") :])
        if credential is None:
            response = Response(
                status_code=401,
                content=b"",
                headers={"WWW-Authenticate": challenge_header()},
            )
            await response(scope, receive, send)
            return
        token = _request_headers.set({"Authorization": f"Bearer {credential}"})
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
    )
    server = create_server(client, catalog)
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    async def handle_mcp(scope, receive, send):
        await session_manager.handle_request(scope, receive, send)

    async def health(request):
        """Liveness *and* upstream reachability.

        This process is a pure proxy: alive-but-cut-off from the REST API is
        indistinguishable from healthy at the container level, and used to be
        indistinguishable here too. 503 when the Hub cannot be reached.

        Do NOT wire this to a compose ``healthcheck:`` with restart semantics —
        restarting the proxy does not fix an unreachable API, it just adds a
        restart loop for the duration of the outage. It is for monitoring.
        """
        try:
            await client.ping()
        except ApiHubError as e:
            return JSONResponse(
                {"status": "degraded", "api_hub": {"url": mcp_settings.api_hub_url, "error": e.detail}},
                status_code=503,
            )
        return JSONResponse({"status": "ok", "api_hub": {"url": mcp_settings.api_hub_url, "status": "ok"}})

    async def oauth_metadata(request):
        return JSONResponse(protected_resource_metadata())

    exchanger = TokenExchanger() if mcp_settings.oauth_enabled else None

    @contextlib.asynccontextmanager
    async def lifespan(app):
        try:
            async with session_manager.run():
                yield
        finally:
            await client.aclose()
            if exchanger is not None:
                await exchanger.aclose()

    starlette_app = Starlette(
        routes=[
            Route("/health", health),
            Route("/.well-known/oauth-protected-resource", oauth_metadata),
            Mount("/mcp", app=handle_mcp),
        ],
        lifespan=lifespan,
    )
    if mcp_settings.oauth_enabled:
        return OAuthGateMiddleware(starlette_app, exchanger)
    return CredentialPassthroughMiddleware(starlette_app)


app = create_http_app()
