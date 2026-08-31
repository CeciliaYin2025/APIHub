"""Browser sign-in for SSO/SAML users — discovery, PKCE, caching, rotation.

The browser leg itself is not exercised here (it needs a human and a real IdP);
everything either side of it is, because that is where a mistake silently costs
someone their credential.
"""

import base64
import hashlib
import json
import time

import httpx
import pytest

from scripts import apihub_login

HUB = "https://hub.example.com/apihub"
ISSUER = "https://isap.example.com/auth"
RESOURCE = "https://hub.example.com/apihub"

AS_META = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/api/v1/authorize",
    "token_endpoint": f"{ISSUER}/api/v1/token",
    "registration_endpoint": f"{ISSUER}/api/v1/register",
}
RS_META = {
    "resource": RESOURCE,
    "authorization_servers": [ISSUER],
    "bearer_methods_supported": ["header"],
}


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.config/apihub/oauth.json."""
    monkeypatch.setenv("APIHUB_CACHE_DIR", str(tmp_path))
    return tmp_path


def _transport(handler):
    return httpx.MockTransport(handler)


def _discovery_handler(extra=None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json=RS_META)
        if url.endswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(200, json=AS_META)
        if extra:
            return extra(request)
        return httpx.Response(404)

    return handler


# ── discovery ───────────────────────────────────────────────────────────────


def test_discovery_walks_from_the_hub_url_to_the_endpoints():
    """The user supplies APIHUB_URL and nothing else; everything else is read."""
    with httpx.Client(transport=_transport(_discovery_handler())) as client:
        resource, as_meta = apihub_login.discover(HUB, client)
    assert resource == RESOURCE
    assert as_meta["token_endpoint"] == f"{ISSUER}/api/v1/token"


def test_discovery_fails_loudly_when_no_authorization_server_is_named():
    def handler(request):
        if str(request.url).endswith("oauth-protected-resource"):
            return httpx.Response(200, json={"resource": RESOURCE})
        return httpx.Response(404)

    with httpx.Client(transport=_transport(handler)) as client:
        with pytest.raises(RuntimeError, match="no authorization server"):
            apihub_login.discover(HUB, client)


# ── PKCE ────────────────────────────────────────────────────────────────────


def test_pkce_challenge_is_the_s256_of_the_verifier():
    """Wrong here and every exchange fails with an opaque invalid_grant."""
    verifier, challenge = apihub_login._pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in challenge and "=" not in verifier  # base64url, unpadded


def test_pkce_pairs_are_not_reused():
    assert apihub_login._pkce_pair()[0] != apihub_login._pkce_pair()[0]


# ── cache ───────────────────────────────────────────────────────────────────


def test_a_live_cached_token_is_returned_without_any_network_call():
    def handler(request):
        raise AssertionError(f"should not have called {request.url}")

    apihub_login._save_cache({HUB: {"access_token": "cached", "expires_at": int(time.time()) + 3600}})
    assert apihub_login.get_token(HUB, transport=_transport(handler)) == "cached"


def test_a_token_inside_the_expiry_margin_is_not_reused():
    """A credential with 60 s left would die partway through a long extraction,
    so it is refreshed rather than handed out."""
    calls = []

    def extra(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 28800, "refresh_token": "r2"})

    apihub_login._save_cache(
        {
            HUB: {
                "access_token": "nearly-dead",
                "expires_at": int(time.time()) + 60,
                "refresh_token": "r1",
                "client_id": "dcr-x",
            }
        }
    )
    assert apihub_login.get_token(HUB, transport=_transport(_discovery_handler(extra))) == "fresh"
    assert any("token" in c for c in calls)


def test_the_cache_file_is_created_private(_isolated_cache):
    apihub_login._save_cache({HUB: {"access_token": "t", "expires_at": 0}})
    mode = apihub_login.cache_path().stat().st_mode & 0o777
    assert mode == 0o600, f"refresh token would be world-readable: {oct(mode)}"


def test_a_corrupt_cache_does_not_crash_the_login():
    apihub_login.cache_path().parent.mkdir(parents=True, exist_ok=True)
    apihub_login.cache_path().write_text("{not json")
    assert apihub_login._load_cache() == {}


# ── refresh / rotation ──────────────────────────────────────────────────────


def test_refresh_rotation_replaces_the_stored_token():
    """isap-auth revokes the whole family when an old refresh token is replayed,
    so keeping the previous one would lock the user out at the next run."""

    def extra(request):
        return httpx.Response(200, json={"access_token": "a2", "expires_in": 28800, "refresh_token": "r2"})

    apihub_login._save_cache(
        {HUB: {"access_token": "a1", "expires_at": 0, "refresh_token": "r1", "client_id": "dcr-x"}}
    )
    apihub_login.get_token(HUB, transport=_transport(_discovery_handler(extra)))
    stored = json.loads(apihub_login.cache_path().read_text())[HUB]
    assert stored["refresh_token"] == "r2"
    assert stored["access_token"] == "a2"


def test_a_dead_refresh_token_falls_through_to_a_fresh_login(monkeypatch):
    """Expired or revoked is a reason to sign in again, not to fail."""

    def extra(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(400, json={"error": "invalid_grant"})
        if request.url.path.endswith("/register"):
            return httpx.Response(201, json={"client_id": "dcr-new"})
        return httpx.Response(404)

    monkeypatch.setattr(apihub_login, "_free_port", lambda: 51999)
    monkeypatch.setattr(apihub_login, "authorize", lambda *a, **k: ("the-code", "the-verifier"))
    monkeypatch.setattr(
        apihub_login,
        "exchange_code",
        lambda *a, **k: {"access_token": "after-relogin", "expires_in": 28800, "refresh_token": "r9"},
    )
    apihub_login._save_cache(
        {HUB: {"access_token": "a1", "expires_at": 0, "refresh_token": "stale", "client_id": "dcr-x"}}
    )
    assert apihub_login.get_token(HUB, transport=_transport(_discovery_handler(extra))) == "after-relogin"


def test_refresh_keeps_the_old_token_when_the_server_returns_none():
    """A response without a new refresh token must not erase the stored one."""

    def extra(request):
        return httpx.Response(200, json={"access_token": "a2", "expires_in": 28800})

    apihub_login._save_cache(
        {HUB: {"access_token": "a1", "expires_at": 0, "refresh_token": "r1", "client_id": "dcr-x"}}
    )
    apihub_login.get_token(HUB, transport=_transport(_discovery_handler(extra)))
    assert json.loads(apihub_login.cache_path().read_text())[HUB]["refresh_token"] == "r1"


# ── registration ────────────────────────────────────────────────────────────


def test_registration_sends_a_loopback_redirect_and_no_secret():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(201, json={"client_id": "dcr-abc"})

    with httpx.Client(transport=_transport(handler)) as client:
        cid = apihub_login.register_client(f"{ISSUER}/api/v1/register", "http://127.0.0.1:51999/callback", client)

    assert cid == "dcr-abc"
    assert seen["redirect_uris"] == ["http://127.0.0.1:51999/callback"]
    assert seen["token_endpoint_auth_method"] == "none"  # public client, PKCE only


def test_an_authorization_server_without_registration_says_what_to_do():
    def extra(request):
        return httpx.Response(404)

    meta = {k: v for k, v in AS_META.items() if k != "registration_endpoint"}

    def handler(request):
        if str(request.url).endswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json=RS_META)
        if str(request.url).endswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(200, json=meta)
        return extra(request)

    with pytest.raises(RuntimeError, match="--client-id"):
        apihub_login.get_token(HUB, transport=_transport(handler))


# ── token binding ───────────────────────────────────────────────────────────


def test_the_exchange_binds_the_token_to_this_hub():
    """RFC 8707: without `resource` the token would be replayable against any
    other resource server sharing this issuer."""
    seen = {}

    def handler(request):
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "a", "expires_in": 60})

    with httpx.Client(transport=_transport(handler)) as client:
        apihub_login.exchange_code(
            AS_META, "dcr-x", "code", "verifier", "http://127.0.0.1:1/callback", RESOURCE, client
        )

    assert seen["resource"] == RESOURCE
    assert seen["code_verifier"] == "verifier"
    assert seen["grant_type"] == "authorization_code"


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_export_prints_a_shell_line(capsys):
    apihub_login._save_cache({HUB: {"access_token": "tok123", "expires_at": int(time.time()) + 3600}})
    assert apihub_login.main(["--base-url", HUB, "--export"]) == 0
    assert capsys.readouterr().out.strip() == "export APIHUB_TOKEN=tok123"


def test_status_reports_without_signing_in(capsys):
    apihub_login._save_cache({HUB: {"access_token": "t", "expires_at": int(time.time()) + 1800, "refresh_token": "r"}})
    assert apihub_login.main(["--base-url", HUB, "--status"]) == 0
    out = capsys.readouterr().out
    assert "valid for 29 min" in out and "refresh token present" in out


def test_status_without_a_cached_token_is_a_failure_exit(capsys):
    assert apihub_login.main(["--base-url", HUB, "--status"]) == 1


def test_missing_base_url_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        apihub_login.main([])
    assert exc.value.code == 2


def test_a_failed_login_reports_rather_than_traces(capsys):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    assert apihub_login.main(["--base-url", HUB], transport=_transport(handler)) == 1
    assert "login failed" in capsys.readouterr().err
