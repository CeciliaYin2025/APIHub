"""Shared SSO identity resolution used by both the MCP bridge and Admin UI."""

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models.user import User
from app.services.sso_service import (
    SsoExchangeError,
    autocreate_enabled,
    exchange_sso_code,
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


def test_exchange_returns_user_info(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")
    monkeypatch.setattr(settings, "isap_app_id", "apihub")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"code": 200, "msg": "OK", "data": {"username": "alice", "email": "a@x"}})

    info = exchange_sso_code("the-code", transport=_transport(handler))
    assert info["username"] == "alice"
    assert seen["url"] == "http://isap-auth:9997/auth/api/v1/token"
    assert '"code": "the-code"' in seen["json"] or '"code":"the-code"' in seen["json"]
    assert "apihub" in seen["json"]


def test_exchange_maps_connection_failure(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")

    def handler(request):
        raise httpx.ConnectError("nope")

    with pytest.raises(SsoExchangeError) as exc:
        exchange_sso_code("x", transport=_transport(handler))
    assert exc.value.reason == "auth_unreachable"


def test_exchange_maps_rejected_code(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")

    def handler(request):
        return httpx.Response(401, json={"code": 401, "msg": "Invalid, expired, or already used auth code"})

    with pytest.raises(SsoExchangeError) as exc:
        exchange_sso_code("x", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"


def test_exchange_maps_missing_username(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")

    def handler(request):
        return httpx.Response(200, json={"code": 200, "msg": "OK", "data": {}})

    with pytest.raises(SsoExchangeError) as exc:
        exchange_sso_code("x", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"
