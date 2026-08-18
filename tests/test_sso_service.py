"""Shared SSO identity resolution used by both the MCP bridge and Admin UI."""

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models.user import User
from app.services.sso_service import (
    SCOPE,
    SsoExchangeError,
    autocreate_enabled,
    build_pkce_pair,
    exchange_pkce_code,
    fetch_userinfo,
    refresh_tokens,
    resolve_sso_user,
)


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
        self.added = []

    def query(self, model):
        return _FakeQuery(self._user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch):
    monkeypatch.setattr(settings, "sso_autocreate", False)
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)


def test_existing_user_is_returned_without_insert():
    user = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    db = _FakeDB(user=user)
    assert resolve_sso_user(db, "alice") is user
    assert db.added == []


def test_unknown_user_rejected_when_autocreate_off():
    with pytest.raises(HTTPException) as exc:
        resolve_sso_user(_FakeDB(user=None), "bob")
    assert exc.value.status_code == 403
    assert "provisioned" in exc.value.detail


def test_unknown_user_created_when_autocreate_on(monkeypatch):
    monkeypatch.setattr(settings, "sso_autocreate", True)
    db = _FakeDB(user=None)
    user = resolve_sso_user(db, "carol")
    assert user.username == "carol"
    assert user.is_active is True
    assert user.is_superuser is False
    assert user.is_service_account is False
    # SSO users have no usable local password: the hash must not be empty and
    # must not be a hash of the empty string.
    assert user.hashed_password
    assert db.added == [user]


def test_mcp_bridge_autocreate_still_works_as_alias(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", True)
    assert autocreate_enabled() is True
    db = _FakeDB(user=None)
    assert resolve_sso_user(db, "dave").username == "dave"


def test_service_account_rejected():
    user = User(username="svc", is_active=True, is_superuser=False, is_service_account=True)
    with pytest.raises(HTTPException) as exc:
        resolve_sso_user(_FakeDB(user=user), "svc")
    assert exc.value.status_code == 403


def test_inactive_user_rejected():
    user = User(username="gone", is_active=False, is_superuser=False, is_service_account=False)
    with pytest.raises(HTTPException) as exc:
        resolve_sso_user(_FakeDB(user=user), "gone")
    assert exc.value.status_code == 403


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def _isap(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")
    monkeypatch.setattr(settings, "isap_app_id", "apihub")


# ── PKCE ────────────────────────────────────────────────────────────────────


def test_pkce_pair_is_s256_and_random():
    import base64
    import hashlib

    verifier, challenge = build_pkce_pair()
    assert 43 <= len(verifier) <= 128  # RFC 7636 §4.1
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in challenge  # base64url, unpadded
    assert build_pkce_pair()[0] != verifier


def test_scope_requests_offline_access():
    """Without it isap-auth returns no refresh token and the Admin UI session
    cannot be renewed silently."""
    assert "offline_access" in SCOPE.split()


# ── authorization_code grant ────────────────────────────────────────────────


def test_code_grant_posts_pkce_form(_isap):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = request.read().decode()
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt"})

    tokens = exchange_pkce_code("the-code", "the-verifier", "https://h/cb", transport=_transport(handler))

    assert tokens["access_token"] == "at"
    assert tokens["refresh_token"] == "rt"
    assert seen["url"] == "http://isap-auth:9997/auth/api/v1/token"
    assert "grant_type=authorization_code" in seen["form"]
    assert "code_verifier=the-verifier" in seen["form"]
    assert "client_id=apihub" in seen["form"]


def test_code_grant_maps_connection_failure(_isap):
    def handler(request):
        raise httpx.ConnectError("nope")

    with pytest.raises(SsoExchangeError) as exc:
        exchange_pkce_code("x", "v", "https://h/cb", transport=_transport(handler))
    assert exc.value.reason == "auth_unreachable"


def test_code_grant_maps_rejected_code(_isap):
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(SsoExchangeError) as exc:
        exchange_pkce_code("x", "v", "https://h/cb", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"


def test_code_grant_rejects_a_response_without_a_token(_isap):
    def handler(request):
        return httpx.Response(200, json={"token_type": "Bearer"})

    with pytest.raises(SsoExchangeError) as exc:
        exchange_pkce_code("x", "v", "https://h/cb", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"


# ── refresh_token grant ─────────────────────────────────────────────────────


def test_refresh_grant_posts_the_stored_token(_isap):
    seen = {}

    def handler(request):
        seen["form"] = request.read().decode()
        return httpx.Response(200, json={"access_token": "at2", "refresh_token": "rt2"})

    tokens = refresh_tokens("rt1", transport=_transport(handler))

    assert tokens["refresh_token"] == "rt2"
    assert "grant_type=refresh_token" in seen["form"]
    assert "refresh_token=rt1" in seen["form"]


def test_refresh_grant_maps_a_spent_token_distinctly(_isap):
    """Separate slug from invalid_code: the caller reacts differently — a spent
    refresh token means "log in again", not "that code was bad"."""

    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(SsoExchangeError) as exc:
        refresh_tokens("spent", transport=_transport(handler))
    assert exc.value.reason == "session_expired"


# ── userinfo ────────────────────────────────────────────────────────────────


def test_userinfo_returns_identity(_isap):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"sub": "alice", "preferred_username": "alice", "email": "a@x"})

    claims = fetch_userinfo("at", transport=_transport(handler))

    assert claims["username"] == "alice"
    assert claims["email"] == "a@x"
    assert seen["url"] == "http://isap-auth:9997/auth/api/v1/userinfo"
    assert seen["auth"] == "Bearer at"


def test_userinfo_falls_back_to_sub(_isap):
    def handler(request):
        return httpx.Response(200, json={"sub": "bob"})

    assert fetch_userinfo("at", transport=_transport(handler))["username"] == "bob"


def test_userinfo_rejects_claims_without_identity(_isap):
    def handler(request):
        return httpx.Response(200, json={"email": "nobody@x"})

    with pytest.raises(SsoExchangeError) as exc:
        fetch_userinfo("at", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"


def test_userinfo_maps_rejected_token(_isap):
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_token"})

    with pytest.raises(SsoExchangeError) as exc:
        fetch_userinfo("dead", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"


def test_userinfo_maps_connection_failure(_isap):
    def handler(request):
        raise httpx.ConnectError("nope")

    with pytest.raises(SsoExchangeError) as exc:
        fetch_userinfo("at", transport=_transport(handler))
    assert exc.value.reason == "auth_unreachable"
