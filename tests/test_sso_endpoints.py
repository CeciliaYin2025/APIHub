"""Admin UI SSO endpoints: /auth/sso/login and /auth/sso/callback."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.api.v1 import auth as auth_module
from app.api.v1.auth import SSO_STATE_COOKIE, sso_callback, sso_login
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


def test_login_redirects_to_authorize_with_state():
    resp = sso_login()
    assert resp.status_code == 302
    parsed = urlparse(resp.headers["location"])
    assert parsed.path == "/auth/api/v1/authorize"
    qs = parse_qs(parsed.query)
    assert qs["app_id"] == ["apihub"]
    assert qs["redirect_uri"] == ["https://h/apihub/api/v1/auth/sso/callback"]
    assert qs["state"][0]


def test_login_sets_httponly_samesite_cookie():
    resp = sso_login()
    cookie = resp.headers["set-cookie"]
    assert SSO_STATE_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


def test_login_state_matches_cookie():
    resp = sso_login()
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    assert state in resp.headers["set-cookie"]


def test_callback_state_mismatch():
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "aaa"}), code="c", state="bbb", db=_FakeDB())
    assert _fragment(resp) == "sso_error=state_mismatch"


def test_callback_missing_cookie():
    resp = sso_callback(_FakeRequest(), code="c", state="bbb", db=_FakeDB())
    assert _fragment(resp) == "sso_error=state_mismatch"


def test_callback_missing_code():
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=missing_code"


def test_callback_auth_unreachable(monkeypatch):
    def boom(code, **kw):
        raise SsoExchangeError("auth_unreachable")

    monkeypatch.setattr(auth_module, "exchange_sso_code", boom)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=auth_unreachable"


def test_callback_invalid_code(monkeypatch):
    def boom(code, **kw):
        raise SsoExchangeError("invalid_code")

    monkeypatch.setattr(auth_module, "exchange_sso_code", boom)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=invalid_code"


def test_callback_not_provisioned(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "bob"})
    monkeypatch.setattr(settings, "sso_autocreate", False)
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=None))
    assert _fragment(resp) == "sso_error=not_provisioned"


def test_callback_not_eligible(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "svc"})
    svc = User(username="svc", is_active=True, is_superuser=False, is_service_account=True)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=svc))
    assert _fragment(resp) == "sso_error=not_eligible"


def test_callback_success_returns_token_fragment(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "alice"})
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=alice))
    parsed = urlparse(resp.headers["location"])
    assert parsed.path == "/apihub/admin"
    assert parsed.fragment.startswith("sso_token=")

    from jose import jwt

    raw = parsed.fragment.split("=", 1)[1]
    claims = jwt.decode(raw, settings.secret_key, algorithms=[settings.algorithm])
    assert claims["sub"] == "alice"


def test_callback_clears_state_cookie(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "alice"})
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=alice))
    cookie = resp.headers["set-cookie"].lower()
    assert SSO_STATE_COOKIE in resp.headers["set-cookie"]
    assert "max-age=0" in cookie or "expires=" in cookie
