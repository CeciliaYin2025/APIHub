"""OAuth 2.1 Resource Server support for the REST API.

isap-auth issues **opaque** access tokens (``secrets.token_urlsafe``, stored
server-side), not JWTs — so validating one is an RFC 7662 introspection call,
never a local signature check, and there is no JWKS shortcut to reach for.

This is the same contract ``mcp_server/oauth.py`` already implements, minus
the identity-bridge hop: the MCP server is a separate process that has to
trade verified claims for a local API Hub JWT, whereas here the Hub *is* the
resource server and maps the claims straight onto a local user.

Everything is inert unless ``OAUTH_RS_ENABLED`` is on and an introspection URL
is configured, so deployments without isap-auth behave exactly as before.
"""

import hashlib
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# token hash -> (monotonic timestamp, claims or None). Negative results are
# cached too: a caller retrying with a dead token must not be able to drive one
# introspection round-trip per request.
_cache: dict[str, tuple[float, dict | None]] = {}

# Advertised to clients via RFC 9728. Kept in sync with mcp_server/oauth.py's
# SCOPES_SUPPORTED, and deliberately WITHOUT offline_access: clients append
# that themselves off the authorization server's own metadata, and listing it
# here would request a refresh token even from clients that cannot store one.
SCOPES_SUPPORTED = ["openid", "profile", "email"]


def enabled() -> bool:
    return settings.oauth_rs_enabled and bool(settings.oauth_introspection_url)


def issuer() -> str:
    """The authorization server clients should be sent to.

    Falls back to ISAP_AUTH_PUBLIC_URL so a deployment that already configured
    Admin UI SSO does not have to repeat the same URL under a second name.
    """
    return (settings.oauth_issuer or settings.isap_auth_public_url).rstrip("/")


def protected_resource_metadata() -> dict:
    """RFC 9728 protected-resource metadata for this API."""
    return {
        "resource": settings.apihub_public_url.rstrip("/"),
        "authorization_servers": [issuer()],
        "scopes_supported": SCOPES_SUPPORTED,
        "bearer_methods_supported": ["header"],
    }


def challenge_header() -> str:
    """WWW-Authenticate value pointing an OAuth client at our metadata."""
    return f'Bearer resource_metadata="{settings.apihub_public_url.rstrip("/")}/.well-known/oauth-protected-resource"'


def _clock() -> float:
    """Seam for tests; production always reads the monotonic clock."""
    return time.monotonic()


def _cache_get(key: str, now: float) -> tuple[bool, dict | None]:
    """(hit, claims). Separate flag because a cached miss is also None."""
    entry = _cache.get(key)
    if entry is not None and (now - entry[0]) < settings.oauth_introspection_cache_seconds:
        return True, entry[1]
    return False, None


def _cache_put(key: str, now: float, claims: dict | None) -> None:
    _cache[key] = (now, claims)
    # Opportunistic sweep, same as mcp_server/oauth.py: no background task, and
    # the dict must not grow without bound across token rotations.
    ttl = settings.oauth_introspection_cache_seconds
    for k in [k for k, v in _cache.items() if (now - v[0]) >= ttl]:
        _cache.pop(k, None)


async def introspect(token: str) -> dict | None:
    """Validate a bearer token against isap-auth; return claims or None.

    Fails closed: an unreachable or malformed authorization server means "no
    identity", never "trust the caller". The result is cached for
    OAUTH_INTROSPECTION_CACHE_SECONDS, which is therefore also the upper bound
    on how long a centrally revoked token keeps working here.
    """
    if not token:
        return None

    key = hashlib.sha256(token.encode()).hexdigest()
    now = _clock()
    hit, cached = _cache_get(key, now)
    if hit:
        return cached

    claims = await _introspect_uncached(token)
    _cache_put(key, now, claims)
    return claims


# One shared client: introspection runs on the hot path of every OAuth request,
# and a per-call AsyncClient would throw away the connection pool each time.
# Closed from the app lifespan (app/main.py).
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def aclose() -> None:
    """Release the shared client. Safe to call when it was never created."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _introspect_uncached(token: str) -> dict | None:
    headers = {}
    if settings.oauth_introspection_secret:
        headers["Authorization"] = f"Bearer {settings.oauth_introspection_secret}"
    try:
        resp = await _http().post(
            settings.oauth_introspection_url,
            data={"token": token},
            headers=headers,
        )
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("OAuth introspection failed: authorization server unreachable or replied non-JSON")
        return None
    if resp.status_code != 200 or not isinstance(payload, dict) or not payload.get("active"):
        return None
    return payload
