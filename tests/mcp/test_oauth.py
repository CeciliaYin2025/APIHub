"""OAuth RS: metadata, challenge header, introspection -> bridge JWT exchange."""

import httpx
import pytest

from mcp_server.config import mcp_settings
from mcp_server.oauth import TokenExchanger, challenge_header, protected_resource_metadata


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "auth_issuer", "https://isap.example.com/auth")
    monkeypatch.setattr(mcp_settings, "introspection_url", "http://isap-auth:9997/auth/api/v1/introspect")
    monkeypatch.setattr(mcp_settings, "introspection_secret", "isecret")
    monkeypatch.setattr(mcp_settings, "bridge_secret", "bsecret")
    monkeypatch.setattr(mcp_settings, "api_hub_url", "http://api:8000")


def test_metadata_and_challenge():
    meta = protected_resource_metadata()
    assert meta == {
        "resource": "https://isap.example.com/apihub/mcp",
        "authorization_servers": ["https://isap.example.com/auth"],
        "scopes_supported": ["openid", "profile", "email"],
    }
    assert 'resource_metadata="https://isap.example.com/apihub/mcp/.well-known/oauth-protected-resource"' in (
        challenge_header()
    )


def test_metadata_advertises_a_non_empty_scope_list():
    """A client that derives an empty scope from this never asks for
    ``offline_access``, so it never gets a refresh token and re-runs the
    browser login every time the access token expires. ``offline_access``
    itself stays out: clients append it from the AS metadata."""
    scopes = protected_resource_metadata()["scopes_supported"]
    assert scopes
    assert "offline_access" not in scopes


def _transport(introspect_response, bridge_response=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if "introspect" in str(request.url):
            assert request.headers.get("Authorization") == "Bearer isecret"
            body = request.content.decode()
            assert "token=" in body and "secret=" not in body
            return httpx.Response(200, json=introspect_response)
        if "sso-token" in str(request.url):
            assert request.headers["X-Bridge-Secret"] == "bsecret"
            return httpx.Response(200, json=bridge_response or {"access_token": "local-jwt", "token_type": "bearer"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_active_token_exchanged_for_local_jwt():
    exchanger = TokenExchanger(transport=_transport({"active": True, "username": "alice", "email": "a@x.com"}))
    jwt_token = await exchanger.exchange("opaque-token")
    assert jwt_token == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_inactive_token_returns_none():
    exchanger = TokenExchanger(transport=_transport({"active": False}))
    assert await exchanger.exchange("bad-token") is None
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_exchange_result_cached_60s():
    calls: list[str] = []
    exchanger = TokenExchanger(transport=_transport({"active": True, "username": "alice", "email": ""}, calls=calls))
    await exchanger.exchange("opaque-token")
    await exchanger.exchange("opaque-token")
    assert len([c for c in calls if "introspect" in c]) == 1  # second call served from cache
    await exchanger.aclose()
