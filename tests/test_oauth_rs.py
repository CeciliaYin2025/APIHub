"""OAuth Resource Server: metadata, challenge header, introspection + caching.

Uses httpx.MockTransport so nothing here needs a live isap-auth (same style as
tests/mcp/test_oauth.py, which covers the MCP server's own copy of this flow).
"""

import httpx
import pytest

import app.services.oauth_rs as oauth_rs
from app.core.config import settings


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "oauth_rs_enabled", True)
    monkeypatch.setattr(settings, "oauth_issuer", "https://isap.example.com/auth")
    monkeypatch.setattr(settings, "oauth_introspection_url", "http://isap-auth:9997/auth/api/v1/introspect")
    monkeypatch.setattr(settings, "oauth_introspection_secret", "isecret")
    monkeypatch.setattr(settings, "oauth_introspection_cache_seconds", 60.0)
    monkeypatch.setattr(settings, "apihub_public_url", "https://isap.example.com/apihub")
    oauth_rs._cache.clear()
    yield
    oauth_rs._cache.clear()


def _install(monkeypatch, handler):
    """Point the module's shared client at a MockTransport and count calls."""
    calls = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_wrapped))
    monkeypatch.setattr(oauth_rs, "_http", lambda: client)
    return calls


def _active(**over):
    claims = {"active": True, "sub": "alice", "username": "alice", "email": "alice@example.com"}
    claims.update(over)
    return lambda request: httpx.Response(200, json=claims)


# ── metadata ────────────────────────────────────────────────────────────────


def test_metadata_shape():
    assert oauth_rs.protected_resource_metadata() == {
        "resource": "https://isap.example.com/apihub",
        "authorization_servers": ["https://isap.example.com/auth"],
        "scopes_supported": ["openid", "profile", "email"],
        "bearer_methods_supported": ["header"],
    }


def test_issuer_falls_back_to_isap_auth_public_url(monkeypatch):
    monkeypatch.setattr(settings, "oauth_issuer", "")
    monkeypatch.setattr(settings, "isap_auth_public_url", "https://fallback.example.com/auth/")
    assert oauth_rs.issuer() == "https://fallback.example.com/auth"


def test_challenge_header_points_at_our_metadata():
    assert (
        oauth_rs.challenge_header()
        == 'Bearer resource_metadata="https://isap.example.com/apihub/.well-known/oauth-protected-resource"'
    )


def test_offline_access_not_advertised():
    """Clients append it off the AS metadata; listing it here would ask for a
    refresh token even from clients that cannot store one."""
    assert "offline_access" not in oauth_rs.SCOPES_SUPPORTED
    assert oauth_rs.SCOPES_SUPPORTED


def test_enabled_requires_both_flag_and_url(monkeypatch):
    assert oauth_rs.enabled()
    monkeypatch.setattr(settings, "oauth_introspection_url", "")
    assert not oauth_rs.enabled()
    monkeypatch.setattr(settings, "oauth_introspection_url", "http://isap-auth:9997/x")
    monkeypatch.setattr(settings, "oauth_rs_enabled", False)
    assert not oauth_rs.enabled()


# ── introspection ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_token_returns_claims(monkeypatch):
    calls = _install(monkeypatch, _active())
    claims = await oauth_rs.introspect("opaque-token")
    assert claims["username"] == "alice"
    assert calls[0].headers["Authorization"] == "Bearer isecret"
    assert "token=opaque-token" in calls[0].content.decode()


@pytest.mark.asyncio
async def test_inactive_token_rejected(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(200, json={"active": False}))
    assert await oauth_rs.introspect("dead") is None


@pytest.mark.asyncio
async def test_non_200_rejected(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(401, json={"error": "invalid_client"}))
    assert await oauth_rs.introspect("t") is None


@pytest.mark.asyncio
async def test_unreachable_authorization_server_fails_closed(monkeypatch):
    def _boom(request):
        raise httpx.ConnectError("down", request=request)

    _install(monkeypatch, _boom)
    assert await oauth_rs.introspect("t") is None


@pytest.mark.asyncio
async def test_non_json_body_fails_closed(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(200, content=b"<html>gateway error</html>"))
    assert await oauth_rs.introspect("t") is None


@pytest.mark.asyncio
async def test_empty_token_makes_no_request(monkeypatch):
    calls = _install(monkeypatch, _active())
    assert await oauth_rs.introspect("") is None
    assert calls == []


# ── caching ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_positive_result_cached(monkeypatch):
    calls = _install(monkeypatch, _active())
    assert await oauth_rs.introspect("t") is not None
    assert await oauth_rs.introspect("t") is not None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_negative_result_cached(monkeypatch):
    """A caller retrying a dead token must not drive one round-trip per request."""
    calls = _install(monkeypatch, lambda request: httpx.Response(200, json={"active": False}))
    assert await oauth_rs.introspect("dead") is None
    assert await oauth_rs.introspect("dead") is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cache_expires(monkeypatch):
    calls = _install(monkeypatch, _active())
    monkeypatch.setattr(settings, "oauth_introspection_cache_seconds", 0.0)
    assert await oauth_rs.introspect("t") is not None
    assert await oauth_rs.introspect("t") is not None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_expired_entries_are_swept(monkeypatch):
    """The cache has no background reaper, so a long-lived process must drop
    aged entries on write — otherwise it grows with every token rotation."""
    _install(monkeypatch, _active())
    # A fake clock jumping a full TTL between calls. Patched on the module, not
    # on `time` itself, which httpx/anyio also read.
    now = [0.0]
    monkeypatch.setattr(oauth_rs, "_clock", lambda: now[0])

    await oauth_rs.introspect("token-old")
    now[0] = 1000.0
    await oauth_rs.introspect("token-newer")
    assert list(oauth_rs._cache.values())[0][1]["username"] == "alice"
    assert len(oauth_rs._cache) == 1


@pytest.mark.asyncio
async def test_distinct_tokens_do_not_share_a_cache_entry(monkeypatch):
    calls = _install(monkeypatch, _active())
    await oauth_rs.introspect("token-a")
    await oauth_rs.introspect("token-b")
    assert len(calls) == 2
