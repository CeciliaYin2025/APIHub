"""OAuth 2.1 Resource Server pieces for the MCP HTTP transport.

Pattern copied from the ITSM-PM MCP server (mcp-technical-report §10.4):
401 challenge with RFC 9728 resource metadata -> isap-auth introspection
(60 s cache) -> identity-bridge exchange for a local API Hub JWT.
"""

import hashlib
import time

import httpx

from mcp_server.config import mcp_settings

_CACHE_TTL = 60.0

# Advertised to the client via RFC 9728. This list must stay non-empty even
# though isap-auth ignores an absent scope: MCP clients derive the scope of
# their authorize request from it, and a client that ends up with an empty
# scope skips the step that appends ``offline_access`` — which is the only
# thing that earns a refresh token (isap-auth issues one solely for that
# scope). Without it every caller re-runs the browser login when the 8 h
# access token expires. ``offline_access`` is deliberately NOT listed here:
# clients add it themselves off the authorization server's metadata, and
# listing it would request it even from clients that cannot store one.
SCOPES_SUPPORTED = ["openid", "profile", "email"]


def protected_resource_metadata() -> dict:
    return {
        "resource": mcp_settings.public_url,
        "authorization_servers": [mcp_settings.auth_issuer],
        "scopes_supported": SCOPES_SUPPORTED,
    }


def challenge_header() -> str:
    return f'Bearer resource_metadata="{mcp_settings.public_url}/.well-known/oauth-protected-resource"'


class TokenExchanger:
    """Validate a Bearer token and produce the credential to forward downstream.

    Introspection happens either way, because the gate needs it: only this
    process can answer an unauthenticated MCP request with the 401 challenge
    that starts the client's OAuth flow. What differs is what gets forwarded:

    - **Passthrough** (no ``MCP_BRIDGE_SECRET``): the caller's own token. The
      API Hub is an OAuth Resource Server in its own right now
      (``app/services/oauth_rs.py``), so it validates the same token itself and
      applies RBAC and audit to the same user. Nothing to share, nothing to
      keep in sync.
    - **Bridge** (``MCP_BRIDGE_SECRET`` set): trade the verified claims for a
      local API Hub JWT via ``POST /api/v1/auth/sso-token``. Kept for
      deployments whose API Hub does not have ``OAUTH_RS_ENABLED`` on yet;
      retiring it is a config change on both sides, not a redeploy.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(timeout=10.0, transport=transport)
        self._cache: dict[str, tuple[float, str | None]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def resolve(self, bearer_token: str) -> str | None:
        """The Authorization value to forward, or None if the token is no good."""
        key = hashlib.sha256(bearer_token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        result = await self._resolve_uncached(bearer_token)
        self._cache[key] = (now, result)
        # opportunistic cleanup of expired entries
        self._cache = {k: v for k, v in self._cache.items() if (now - v[0]) < _CACHE_TTL}
        return result

    async def _resolve_uncached(self, bearer_token: str) -> str | None:
        claims = await self._introspect(bearer_token)
        if claims is None:
            return None
        if not mcp_settings.bridge_secret:
            return bearer_token
        return await self._bridge(claims)

    async def _introspect(self, bearer_token: str) -> dict | None:
        headers = {}
        if mcp_settings.introspection_secret:
            headers["Authorization"] = f"Bearer {mcp_settings.introspection_secret}"
        try:
            resp = await self._http.post(
                mcp_settings.introspection_url,
                data={"token": bearer_token},
                headers=headers,
            )
            claims = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if resp.status_code != 200 or not claims.get("active"):
            return None
        return claims

    async def _bridge(self, claims: dict) -> str | None:
        try:
            bridge = await self._http.post(
                f"{mcp_settings.api_hub_url.rstrip('/')}/api/v1/auth/sso-token",
                json={"username": claims.get("username", ""), "email": claims.get("email") or ""},
                headers={"X-Bridge-Secret": mcp_settings.bridge_secret},
            )
        except httpx.HTTPError:
            return None
        if bridge.status_code != 200:
            return None
        return bridge.json().get("access_token")
