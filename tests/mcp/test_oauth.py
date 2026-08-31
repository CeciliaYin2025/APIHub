"""OAuth RS: metadata, challenge header, introspection -> bridge JWT exchange."""

import httpx
import pytest

from mcp_server.config import mcp_settings
from mcp_server.oauth import Exchange, TokenExchanger, challenge_header, protected_resource_metadata


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "auth_issuer", "https://isap.example.com/auth")
    monkeypatch.setattr(mcp_settings, "introspection_url", "http://isap-auth:9997/auth/api/v1/introspect")
    monkeypatch.setattr(mcp_settings, "introspection_secret", "isecret")
    monkeypatch.setattr(mcp_settings, "bridge_secret", "bsecret")
    monkeypatch.setattr(mcp_settings, "api_hub_url", "http://api:8000")
    monkeypatch.setattr(mcp_settings, "oauth_allowed_client_ids", "")


def test_metadata_and_challenge():
    meta = protected_resource_metadata()
    assert meta == {
        "resource": "https://isap.example.com/apihub/mcp",
        "authorization_servers": ["https://isap.example.com/auth"],
        "bearer_methods_supported": ["header"],
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


def test_scopes_are_advertised_when_configured(monkeypatch):
    """Least privilege: tell the client which scopes to ask for instead of
    letting it request everything the AS offers."""
    monkeypatch.setattr(mcp_settings, "oauth_scopes", "apihub:read apihub:query")
    assert protected_resource_metadata()["scopes_supported"] == ["apihub:read", "apihub:query"]
    assert 'scope="apihub:read apihub:query"' in challenge_header()


def test_default_scopes_fill_in_when_not_configured():
    """Empty config still advertises the built-in list — see SCOPES_SUPPORTED."""
    assert protected_resource_metadata()["scopes_supported"] == ["openid", "profile", "email"]
    assert 'scope="openid profile email"' in challenge_header()


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


AUD = "https://isap.example.com/apihub/mcp"


@pytest.mark.asyncio
async def test_active_token_exchanged_for_local_jwt():
    """Bridge mode: MCP_BRIDGE_SECRET set, so claims are traded for a local JWT."""
    exchanger = TokenExchanger(
        transport=_transport({"active": True, "username": "alice", "email": "a@x.com", "aud": AUD})
    )
    result = await exchanger.exchange("opaque-token")
    assert result.jwt == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_inactive_token_returns_none():
    exchanger = TokenExchanger(transport=_transport({"active": False}))
    assert await exchanger.exchange("bad-token") == Exchange(jwt=None, status=401)
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_token_issued_for_another_resource_is_rejected():
    """An active isap-auth token minted for a different app on the same gateway.

    Also the proof that the check tightens back up on its own the day the AS
    starts supplying an audience — no config change, no redeploy."""
    calls: list[str] = []
    exchanger = TokenExchanger(
        transport=_transport(
            {"active": True, "username": "alice", "aud": "https://isap.example.com/itpm/mcp"},
            calls=calls,
        )
    )
    assert (await exchanger.exchange("other-app-token")).status == 401
    assert not [c for c in calls if "sso-token" in c]  # never reached the identity bridge
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_token_without_any_audience_claim_is_accepted():
    """isap-auth returns no aud/resource claim of any kind, so rejecting
    audience-less tokens rejects every caller — which is exactly what took the
    MCP server down. With nothing to enforce, the audience check stands down;
    see mcp_server/oauth.py::token_is_for_this_server."""
    exchanger = TokenExchanger(transport=_transport({"active": True, "username": "alice"}))
    assert (await exchanger.exchange("audience-less-token")).jwt == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_audience_less_token_gated_on_client_id_allowlist(monkeypatch):
    """The opt-in fallback for a deployment whose clients are statically
    registered (DCR clients cannot be listed — they mint an id per install)."""
    monkeypatch.setattr(mcp_settings, "oauth_allowed_client_ids", "apihub-mcp, other-client")
    calls: list[str] = []
    exchanger = TokenExchanger(
        transport=_transport({"active": True, "username": "alice", "client_id": "someone-else"}, calls=calls)
    )
    assert (await exchanger.exchange("stranger-token")).status == 401
    assert not [c for c in calls if "sso-token" in c]  # never reached the identity bridge

    allowed = TokenExchanger(transport=_transport({"active": True, "username": "alice", "client_id": "apihub-mcp"}))
    assert (await allowed.exchange("listed-token")).jwt == "local-jwt"
    await allowed.aclose()
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_audience_accepted_from_the_endpoint_url_below_the_resource_id():
    """Our resource identifier is https://isap.example.com/apihub/mcp, but the
    endpoint clients actually post to is .../apihub/mcp/mcp/. A client that
    reads the MCP spec's "canonical URI of the MCP server" as the latter is
    still naming us, and Claude Code sends whichever it picked at BOTH the
    authorize and the token leg — so getting this wrong locks it out at its
    next login, with the stand-down unable to help because an aud is present."""
    exchanger = TokenExchanger(
        transport=_transport({"active": True, "username": "alice", "aud": "https://isap.example.com/apihub/mcp/mcp/"})
    )
    assert (await exchanger.exchange("endpoint-url-aud")).jwt == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_sibling_resources_do_not_slip_through_the_prefix():
    """The prefix stops at a path segment, which is the whole reason the two
    stacks stay separate: apihub must not accept apihub-test's tokens."""
    for stranger in (
        "https://isap.example.com/apihub-test/mcp",
        "https://isap.example.com/apihub/mcpx",
        "https://isap.example.com/apihub",
    ):
        exchanger = TokenExchanger(transport=_transport({"active": True, "username": "alice", "aud": stranger}))
        assert (await exchanger.exchange(f"tok-{stranger}")).status == 401, stranger
        await exchanger.aclose()


@pytest.mark.asyncio
async def test_audience_accepted_from_list_valued_aud():
    exchanger = TokenExchanger(
        transport=_transport(
            {
                "active": True,
                "username": "alice",
                "aud": ["https://isap.example.com/itpm/mcp", "https://isap.example.com/apihub/mcp"],
            }
        )
    )
    assert (await exchanger.exchange("multi-aud-token")).jwt == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_audience_accepted_from_space_delimited_resource_claim():
    exchanger = TokenExchanger(
        transport=_transport(
            {
                "active": True,
                "username": "alice",
                "resource": "https://isap.example.com/itpm/mcp https://isap.example.com/apihub/mcp",
            }
        )
    )
    assert (await exchanger.exchange("resource-claim-token")).jwt == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_explicit_audience_setting_overrides_public_url(monkeypatch):
    monkeypatch.setattr(mcp_settings, "oauth_audience", "apihub-mcp")
    exchanger = TokenExchanger(transport=_transport({"active": True, "username": "alice", "aud": "apihub-mcp"}))
    assert (await exchanger.exchange("token")).jwt == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_unreachable_upstream_is_retryable_and_not_cached():
    """A network blip is not a verdict on the token — caching it would lock the
    caller out for 60 s after the upstream recovered."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ConnectError("boom")

    exchanger = TokenExchanger(transport=httpx.MockTransport(handler))
    assert await exchanger.exchange("tok") == Exchange(jwt=None, status=503)
    await exchanger.exchange("tok")
    assert len(calls) == 2
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_bridge_refusal_is_forbidden_not_unauthorized():
    """The token authenticated fine; the person simply has no account here."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "introspect" in str(request.url):
            return httpx.Response(200, json={"active": True, "username": "alice", "aud": AUD})
        return httpx.Response(403, json={"detail": "user not provisioned"})

    exchanger = TokenExchanger(transport=httpx.MockTransport(handler))
    assert await exchanger.exchange("tok") == Exchange(jwt=None, status=403)
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_bridge_server_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        if "introspect" in str(request.url):
            return httpx.Response(200, json={"active": True, "username": "alice", "aud": AUD})
        return httpx.Response(500, json={"detail": "boom"})

    exchanger = TokenExchanger(transport=httpx.MockTransport(handler))
    assert (await exchanger.exchange("tok")).status == 503
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_result_cached_60s():
    calls: list[str] = []
    exchanger = TokenExchanger(
        transport=_transport({"active": True, "username": "alice", "email": "", "aud": AUD}, calls=calls)
    )
    await exchanger.exchange("opaque-token")
    await exchanger.exchange("opaque-token")
    assert len([c for c in calls if "introspect" in c]) == 1  # second call served from cache
    await exchanger.aclose()


# ── passthrough mode (no bridge secret) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_passthrough_forwards_the_callers_own_token(monkeypatch):
    """With no bridge secret the API Hub validates the same token itself, so the
    caller's credential is forwarded unchanged and the bridge is never called."""
    monkeypatch.setattr(mcp_settings, "bridge_secret", "")
    calls: list[str] = []
    exchanger = TokenExchanger(transport=_transport({"active": True, "username": "alice", "aud": AUD}, calls=calls))

    assert (await exchanger.exchange("opaque-token")).jwt == "opaque-token"
    assert not [c for c in calls if "sso-token" in c]
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_passthrough_still_gates_on_introspection(monkeypatch):
    """Forwarding the token does NOT mean skipping validation: this process is
    the only one that can answer with the 401 challenge an MCP client needs."""
    monkeypatch.setattr(mcp_settings, "bridge_secret", "")
    calls: list[str] = []
    exchanger = TokenExchanger(transport=_transport({"active": False}, calls=calls))

    result = await exchanger.exchange("dead-token")
    assert result.jwt is None
    assert result.status == 401
    assert [c for c in calls if "introspect" in c]
    await exchanger.aclose()
