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


def protected_resource_metadata() -> dict:
    return {
        "resource": mcp_settings.public_url,
        "authorization_servers": [mcp_settings.auth_issuer],
    }


def challenge_header() -> str:
    return f'Bearer resource_metadata="{mcp_settings.public_url}/.well-known/oauth-protected-resource"'


class TokenExchanger:
    """Introspect a Bearer token and exchange verified claims for a local JWT."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(timeout=10.0, transport=transport)
        self._cache: dict[str, tuple[float, str | None]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def exchange(self, bearer_token: str) -> str | None:
        key = hashlib.sha256(bearer_token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        result = await self._exchange_uncached(bearer_token)
        self._cache[key] = (now, result)
        # opportunistic cleanup of expired entries
        self._cache = {k: v for k, v in self._cache.items() if (now - v[0]) < _CACHE_TTL}
        return result

    async def _exchange_uncached(self, bearer_token: str) -> str | None:
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
