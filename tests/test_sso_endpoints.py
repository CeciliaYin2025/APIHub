"""Admin UI SSO endpoints: OAuth 2.1 authorization code + PKCE, and refresh."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.api.v1 import auth as auth_module
from app.api.v1.auth import (
    SSO_REFRESH_COOKIE,
    SSO_STATE_COOKIE,
    SSO_VERIFIER_COOKIE,
    sso_callback,
    sso_login,
    sso_logout,
    sso_refresh,
)
from app.core.config import settings
from app.models.user import User
from app.services.sso_service import SsoExchangeError


class _FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, user=None):
        self._user = user

    def query(self, model):
        return _FakeQuery(self._user)


@pytest.fixture(autouse=True)
def _sso_on(monkeypatch):
    monkeypatch.setattr(settings, "admin_sso_enabled", True)
    monkeypatch.setattr(settings, "apihub_public_url", "https://h/apihub")
    monkeypatch.setattr(settings, "isap_auth_public_url", "https://h/auth")
    monkeypatch.setattr(settings, "isap_app_id", "apihub")
    monkeypatch.setattr(settings, "root_path", "/apihub")
    monkeypatch.setattr(settings, "api_prefix", "/api/v1")


def _fragment(response):
    return urlparse(response.headers["location"]).fragment


def test_login_404_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_sso_enabled", False)
    with pytest.raises(HTTPException) as exc:
        sso_login()
    assert exc.value.status_code == 404


def test_callback_404_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_sso_enabled", False)
    with pytest.raises(HTTPException) as exc:
        sso_callback(_FakeRequest(), code="c", state="s", db=_FakeDB())
    assert exc.value.status_code == 404


def _cookies(response):
    return "\n".join(response.headers.getlist("set-cookie"))


def test_login_redirects_to_authorize_with_pkce():
    resp = sso_login()
    assert resp.status_code == 302
    parsed = urlparse(resp.headers["location"])
    assert parsed.path == "/auth/api/v1/authorize"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["apihub"]
    assert qs["response_type"] == ["code"]
    assert qs["redirect_uri"] == ["https://h/apihub/api/v1/auth/sso/callback"]
    assert qs["state"][0]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"][0]


def test_login_requests_offline_access():
    """No offline_access, no refresh token — and then the Admin UI session dies
    with the access token instead of renewing silently."""
    qs = parse_qs(urlparse(sso_login().headers["location"]).query)
    assert "offline_access" in qs["scope"][0].split()


def test_login_challenge_is_the_hash_not_the_verifier():
    """The verifier must never appear in the redirect: that is the whole point
    of PKCE — what travels through the browser cannot redeem the code."""
    import base64
    import hashlib

    resp = sso_login()
    location = resp.headers["location"]
    challenge = parse_qs(urlparse(location).query)["code_challenge"][0]
    verifier = [
        c.split("=", 1)[1].split(";")[0] for c in resp.headers.getlist("set-cookie") if SSO_VERIFIER_COOKIE in c
    ][0]

    assert verifier not in location
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_login_sets_httponly_samesite_cookies():
    cookies = _cookies(sso_login())
    for name in (SSO_STATE_COOKIE, SSO_VERIFIER_COOKIE):
        line = [c for c in cookies.split("\n") if name in c][0]
        assert "HttpOnly" in line
        assert "samesite=lax" in line.lower()


def test_login_state_matches_cookie():
    resp = sso_login()
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    assert state in _cookies(resp)


COOKIES = {SSO_STATE_COOKIE: "s", SSO_VERIFIER_COOKIE: "v"}


def _stub_exchange(monkeypatch, username="alice", refresh="r1", verifier_seen=None):
    def _exchange(code, verifier, redirect_uri, **kw):
        if verifier_seen is not None:
            verifier_seen.append(verifier)
        return {"access_token": "at", "refresh_token": refresh} if refresh else {"access_token": "at"}

    monkeypatch.setattr(auth_module, "exchange_pkce_code", _exchange)
    monkeypatch.setattr(auth_module, "fetch_userinfo", lambda token, **kw: {"username": username})


def test_callback_state_mismatch():
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "aaa"}), code="c", state="bbb", db=_FakeDB())
    assert _fragment(resp) == "sso_error=state_mismatch"


def test_callback_missing_cookie():
    resp = sso_callback(_FakeRequest(), code="c", state="bbb", db=_FakeDB())
    assert _fragment(resp) == "sso_error=state_mismatch"


def test_callback_missing_code():
    resp = sso_callback(_FakeRequest(COOKIES), code="", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=missing_code"


def test_callback_without_verifier_refuses_to_redeem(monkeypatch):
    """No verifier means the flow cannot be tied to the browser that started
    it — redeeming anyway would hand back exactly the guarantee PKCE adds."""
    called = []
    monkeypatch.setattr(auth_module, "exchange_pkce_code", lambda *a, **k: called.append(a))
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=state_mismatch"
    assert called == []


def test_callback_sends_the_stored_verifier(monkeypatch):
    seen = []
    _stub_exchange(monkeypatch, verifier_seen=seen)
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB(user=alice))
    assert seen == ["v"]


def test_callback_auth_unreachable(monkeypatch):
    def boom(*a, **kw):
        raise SsoExchangeError("auth_unreachable")

    monkeypatch.setattr(auth_module, "exchange_pkce_code", boom)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=auth_unreachable"


def test_callback_invalid_code(monkeypatch):
    def boom(*a, **kw):
        raise SsoExchangeError("invalid_code")

    monkeypatch.setattr(auth_module, "exchange_pkce_code", boom)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=invalid_code"


def test_callback_userinfo_failure_surfaces(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_pkce_code", lambda *a, **kw: {"access_token": "at"})

    def boom(*a, **kw):
        raise SsoExchangeError("invalid_code")

    monkeypatch.setattr(auth_module, "fetch_userinfo", boom)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=invalid_code"


def test_callback_not_provisioned(monkeypatch):
    _stub_exchange(monkeypatch, username="bob")
    monkeypatch.setattr(settings, "sso_autocreate", False)
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB(user=None))
    assert _fragment(resp) == "sso_error=not_provisioned"


def test_callback_not_eligible(monkeypatch):
    _stub_exchange(monkeypatch, username="svc")
    svc = User(username="svc", is_active=True, is_superuser=False, is_service_account=True)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB(user=svc))
    assert _fragment(resp) == "sso_error=not_eligible"


def test_callback_success_returns_token_fragment(monkeypatch):
    _stub_exchange(monkeypatch)
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB(user=alice))
    parsed = urlparse(resp.headers["location"])
    assert parsed.path == "/apihub/admin"
    assert parsed.fragment.startswith("sso_token=")

    from jose import jwt

    raw = parsed.fragment.split("=", 1)[1]
    claims = jwt.decode(raw, settings.secret_key, algorithms=[settings.algorithm])
    assert claims["sub"] == "alice"


def test_callback_stores_refresh_token_in_a_scoped_httponly_cookie(monkeypatch):
    _stub_exchange(monkeypatch)
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB(user=alice))

    line = [c for c in resp.headers.getlist("set-cookie") if SSO_REFRESH_COOKIE in c][0]
    assert "r1" in line
    assert "HttpOnly" in line
    # Strict, not Lax: a cross-site request that spent the rotation would make
    # the real tab's next refresh look like a replay, killing the token family.
    assert "samesite=strict" in line.lower()
    # Scoped to the SSO routes so it does not ride along on every API call.
    assert "path=/apihub/api/v1/auth/sso" in line.lower()


def test_callback_without_refresh_token_sets_no_cookie(monkeypatch):
    """isap-auth only issues one for offline_access; absence must not crash."""
    _stub_exchange(monkeypatch, refresh=None)
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB(user=alice))
    assert not [c for c in resp.headers.getlist("set-cookie") if SSO_REFRESH_COOKIE in c]


def test_callback_clears_state_and_verifier_cookies(monkeypatch):
    _stub_exchange(monkeypatch)
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest(COOKIES), code="c", state="s", db=_FakeDB(user=alice))
    for name in (SSO_STATE_COOKIE, SSO_VERIFIER_COOKIE):
        line = [c for c in resp.headers.getlist("set-cookie") if name in c][0].lower()
        assert "max-age=0" in line or "expires=" in line


# ── refresh / logout ────────────────────────────────────────────────────────


def _stub_refresh(monkeypatch, username="alice", new_refresh="r2"):
    body = {"access_token": "at2"}
    if new_refresh:
        body["refresh_token"] = new_refresh
    monkeypatch.setattr(auth_module, "refresh_tokens", lambda token, **kw: body)
    monkeypatch.setattr(auth_module, "fetch_userinfo", lambda token, **kw: {"username": username})


def test_refresh_404_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_sso_enabled", False)
    with pytest.raises(HTTPException) as exc:
        sso_refresh(_FakeRequest({SSO_REFRESH_COOKIE: "r1"}), db=_FakeDB())
    assert exc.value.status_code == 404


def test_refresh_without_cookie_is_401():
    with pytest.raises(HTTPException) as exc:
        sso_refresh(_FakeRequest(), db=_FakeDB())
    assert exc.value.status_code == 401


def test_refresh_returns_a_new_jwt(monkeypatch):
    _stub_refresh(monkeypatch)
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_refresh(_FakeRequest({SSO_REFRESH_COOKIE: "r1"}), db=_FakeDB(user=alice))

    assert resp.status_code == 200
    import json

    from jose import jwt

    raw = json.loads(resp.body)["access_token"]
    assert jwt.decode(raw, settings.secret_key, algorithms=[settings.algorithm])["sub"] == "alice"


def test_refresh_stores_the_rotated_token(monkeypatch):
    """isap-auth invalidates the presented token on every redeem. Keeping the
    old one would replay it next time, which revokes the whole family."""
    _stub_refresh(monkeypatch)
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_refresh(_FakeRequest({SSO_REFRESH_COOKIE: "r1"}), db=_FakeDB(user=alice))
    line = [c for c in resp.headers.getlist("set-cookie") if SSO_REFRESH_COOKIE in c][0]
    assert "r2" in line


def test_refresh_failure_clears_the_cookie(monkeypatch):
    def boom(*a, **kw):
        raise SsoExchangeError("session_expired")

    monkeypatch.setattr(auth_module, "refresh_tokens", boom)
    resp = sso_refresh(_FakeRequest({SSO_REFRESH_COOKIE: "spent"}), db=_FakeDB())
    assert resp.status_code == 401
    line = [c for c in resp.headers.getlist("set-cookie") if SSO_REFRESH_COOKIE in c][0].lower()
    assert "max-age=0" in line or "expires=" in line


def test_refresh_for_a_deprovisioned_user_clears_the_cookie(monkeypatch):
    """A live ISAP session whose API Hub account was removed must not keep
    minting JWTs — and must not keep retrying either."""
    _stub_refresh(monkeypatch, username="gone")
    monkeypatch.setattr(settings, "sso_autocreate", False)
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)
    resp = sso_refresh(_FakeRequest({SSO_REFRESH_COOKIE: "r1"}), db=_FakeDB(user=None))
    assert resp.status_code == 401


def test_logout_clears_the_refresh_cookie():
    resp = sso_logout()
    line = [c for c in resp.headers.getlist("set-cookie") if SSO_REFRESH_COOKIE in c][0].lower()
    assert "max-age=0" in line or "expires=" in line
    assert "path=/apihub/api/v1/auth/sso" in line
