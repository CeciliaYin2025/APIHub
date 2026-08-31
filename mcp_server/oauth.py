"""OAuth 2.1 Resource Server pieces for the MCP HTTP transport.

Pattern copied from the ITSM-PM MCP server (mcp-technical-report §10.4):
401 challenge with RFC 9728 resource metadata -> isap-auth introspection
(60 s cache) -> forward the caller's own token (or, in legacy bridge mode,
exchange it for a local API Hub JWT).
"""

import hashlib
import time
from dataclasses import dataclass

import httpx

from mcp_server.config import mcp_settings

_CACHE_TTL = 60.0

# Advertised to the client via RFC 9728 when MCP_OAUTH_SCOPES is empty. This
# list must stay non-empty even though isap-auth ignores an absent scope: MCP
# clients derive the scope of their authorize request from it, and a client
# that ends up with an empty scope skips the step that appends
# ``offline_access`` — which is the only thing that earns a refresh token
# (isap-auth issues one solely for that scope). Without it every caller
# re-runs the browser login when the 8 h access token expires.
# ``offline_access`` is deliberately NOT listed here: clients add it
# themselves off the authorization server's metadata, and listing it would
# request it even from clients that cannot store one.
SCOPES_SUPPORTED = ["openid", "profile", "email"]


@dataclass(frozen=True)
class Exchange:
    """Outcome of validating a caller's Bearer token.

    ``jwt`` is the credential to forward downstream — the caller's own token
    in passthrough mode, a minted local API Hub JWT in bridge mode.

    ``status`` is the HTTP status the gate should answer with when ``jwt`` is
    None, and it is the whole point of this type: the three failure modes are
    not interchangeable. 401 says "get a different token", 403 says "your token
    is fine but you may not use this server", 503 says "nothing you do will
    help right now" — answering 401 for the last one sends the client round the
    entire authorization flow for an outage.
    """

    jwt: str | None = None
    status: int = 401

    @property
    def retryable(self) -> bool:
        """Upstream failure rather than a verdict — must not be cached."""
        return self.status >= 500


def _scopes() -> list[str]:
    configured = [s for s in mcp_settings.oauth_scopes.replace(",", " ").split() if s]
    return configured or SCOPES_SUPPORTED


def protected_resource_metadata() -> dict:
    return {
        "resource": mcp_settings.public_url,
        "authorization_servers": [mcp_settings.auth_issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": _scopes(),
    }


# isap-auth is shared by every app on the gateway, so "the token introspects as
# active" says nothing about who it was minted for. Per the MCP authorization
# spec a resource server MUST reject tokens not issued for itself; without this
# any other app's valid token would be accepted here.
# Which claim carries the audience depends on the AS, so accept the usual set.
_AUDIENCE_CLAIMS = ("aud", "resource", "resources", "audience")


def _audience_values(claims: dict) -> set[str]:
    values: set[str] = set()
    for name in _AUDIENCE_CLAIMS:
        raw = claims.get(name)
        if isinstance(raw, str):
            values.update(raw.split())  # RFC 8707 allows a space-delimited list
        elif isinstance(raw, (list, tuple)):
            values.update(v for v in raw if isinstance(v, str))
    return values


def _names_this_server(expected: str, value: str) -> bool:
    """Whether one audience value names this server.

    Exact match, or anything *below* our resource identifier. The second half
    is not laxity, it is the two URLs this server legitimately answers to: RFC
    9728 makes the identifier the ``resource`` from our metadata
    (``.../apihub/mcp``), while a client that takes the MCP spec's "canonical
    URI of the server" literally sends the endpoint it actually posts to
    (``.../apihub/mcp/mcp/``). Both name us; rejecting the second would lock
    out whichever clients read it that way, and we cannot tell in advance which
    those are.

    The boundary is a path segment, so a sibling never slips through: with
    ``.../apihub/mcp`` expected, ``.../apihub-test/mcp`` and ``.../apihub/mcpx``
    both fail — which is what keeps the two stacks separate.
    """
    value = value.rstrip("/")
    return value == expected or value.startswith(expected + "/")


def audience_matches(claims: dict) -> bool:
    """True when the token names this server in an audience claim.

    Only meaningful when the claims actually carry one — see
    ``token_is_for_this_server`` for the case where they do not.
    """
    expected = (mcp_settings.oauth_audience or mcp_settings.public_url).rstrip("/")
    if not expected:
        return False  # nothing to compare against — fail closed
    return any(_names_this_server(expected, v) for v in _audience_values(claims))


def _allowed_client_ids() -> set[str]:
    raw = mcp_settings.oauth_allowed_client_ids.replace(",", " ").split()
    return {c for c in raw if c}


def token_is_for_this_server(claims: dict) -> bool:
    """Whether this token may be used against *this* resource server.

    Audience binding is the check the MCP spec asks for, and it is enforced
    whenever the authorization server gives us something to enforce it with.
    **isap-auth does not**: it ignores the ``resource`` parameter clients send
    and its introspection response carries no ``aud``/``resource`` claim at all
    (verified 2026-08-31 against a live token). Rejecting audience-less claims
    therefore rejected *every* caller — the MCP server answered 401 to every
    request from the moment this check shipped, which is not a security posture,
    just an outage.

    So the rule is: enforce the audience when the AS supplies one, and when it
    supplies none, fall back to ``MCP_OAUTH_ALLOWED_CLIENT_IDS``. The fallback
    is deliberately opt-in and empty by default, because clients that register
    through DCR — Claude Code among them — mint a fresh ``client_id`` per
    install and cannot be listed in advance. With an empty list any active
    isap-auth token is accepted here, which is the pre-3221ed0 behaviour; what
    limits the damage is that authorization still comes from the API Hub's own
    RBAC for that user, never from the token.

    The day isap-auth starts echoing an audience, this tightens back up on its
    own — no config change, no redeploy.
    """
    if (allowed := _allowed_client_ids()) and str(claims.get("client_id") or "") not in allowed:
        return False
    if not _audience_values(claims):
        return True
    return audience_matches(claims)


def challenge_header() -> str:
    """The ``WWW-Authenticate`` value for a 401.

    Naming the required scopes lets a client ask for those and nothing else;
    without it the spec has clients fall back to requesting every scope the
    authorization server advertises.
    """
    challenge = f'Bearer resource_metadata="{mcp_settings.public_url}/.well-known/oauth-protected-resource"'
    if scopes := _scopes():
        challenge += f', scope="{" ".join(scopes)}"'
    return challenge


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
        self._cache: dict[str, tuple[float, Exchange]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def exchange(self, bearer_token: str) -> Exchange:
        key = hashlib.sha256(bearer_token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        result = await self._exchange_uncached(bearer_token)
        if not result.retryable:
            # Only definitive outcomes are cached. Caching an upstream outage
            # would keep rejecting the caller for the rest of the TTL after the
            # upstream came back.
            self._cache[key] = (now, result)
        # opportunistic cleanup of expired entries
        self._cache = {k: v for k, v in self._cache.items() if (now - v[0]) < _CACHE_TTL}
        return result

    async def _exchange_uncached(self, bearer_token: str) -> Exchange:
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
            return Exchange(status=503)
        if resp.status_code != 200:
            # The AS could not answer — a misconfigured introspection secret
            # lands here too, and neither is the caller's fault.
            return Exchange(status=503)
        if not claims.get("active") or not token_is_for_this_server(claims):
            return Exchange(status=401)
        if not mcp_settings.bridge_secret:
            return Exchange(jwt=bearer_token)
        return await self._bridge(claims)

    async def _bridge(self, claims: dict) -> Exchange:
        try:
            bridge = await self._http.post(
                f"{mcp_settings.api_hub_url.rstrip('/')}/api/v1/auth/sso-token",
                json={"username": claims.get("username", ""), "email": claims.get("email") or ""},
                headers={"X-Bridge-Secret": mcp_settings.bridge_secret},
            )
        except httpx.HTTPError:
            return Exchange(status=503)
        if bridge.status_code >= 500:
            return Exchange(status=503)
        if bridge.status_code != 200:
            # The token was good; the Hub declined to mint a local identity for
            # this person (no account, SSO_AUTOCREATE off, disabled user).
            return Exchange(status=403)
        return Exchange(jwt=bridge.json().get("access_token"))
