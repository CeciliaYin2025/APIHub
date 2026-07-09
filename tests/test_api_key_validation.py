"""Tests for API key validation (resolve_api_key)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_api_key
from app.models.user import ApiKey, Permission, Role, User
from app.services.auth_service import ApiKeyError, resolve_api_key

RAW = "ahk_testkey123"


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row
        self.committed = False

    def query(self, _model):
        return _FakeQuery(self._row)

    def commit(self):
        self.committed = True


def _service_user(is_active=True):
    role = Role(name="r", permissions=[Permission(resource="query:*", action="read")])
    return User(username="svc", is_active=is_active, is_service_account=True, is_superuser=False, roles=[role])


def _key(**over):
    user = over.pop("user", _service_user())
    defaults = dict(
        id=1,
        user_id=1,
        name="k",
        key_prefix="ahk_test",
        key_hash=hash_api_key(RAW),
        enabled=True,
        expires_at=None,
        ip_allowlist=None,
        rate_limit_per_minute=None,
    )
    defaults.update(over)
    k = ApiKey(**defaults)
    k.user = user
    return k


def test_valid_key_returns_principal():
    k = _key()
    db = _FakeDB(k)
    p = resolve_api_key(db, RAW, "10.0.0.1")
    assert p.type == "api_key"
    assert p.user.username == "svc"
    assert p.key_id == 1
    # Verify side-effect: last_used_at was updated and commit() was called
    assert db.committed is True
    assert k.last_used_at is not None


def test_unknown_key_raises_401():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(None), RAW, "10.0.0.1")
    assert e.value.status_code == 401


def test_disabled_key_raises_401():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(enabled=False)), RAW, "10.0.0.1")
    assert e.value.status_code == 401
    assert "disabled" in e.value.detail


def test_expired_key_raises_401():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(expires_at=past)), RAW, "10.0.0.1")
    assert "expired" in e.value.detail


def test_future_expiry_is_valid():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    p = resolve_api_key(_FakeDB(_key(expires_at=future)), RAW, "10.0.0.1")
    assert p.type == "api_key"


def test_ip_not_in_allowlist_raises_403():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(ip_allowlist=["10.1.0.0/16"])), RAW, "192.168.0.5")
    assert e.value.status_code == 403


def test_ip_in_allowlist_is_valid():
    p = resolve_api_key(_FakeDB(_key(ip_allowlist=["10.1.0.0/16"])), RAW, "10.1.2.3")
    assert p.type == "api_key"


def test_inactive_service_account_raises_401():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(user=_service_user(is_active=False))), RAW, "10.0.0.1")
    assert e.value.status_code == 401
