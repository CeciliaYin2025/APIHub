"""Tests for API key service (create/list/revoke)."""

import pytest

from app.models.user import ApiKey, User
from app.services.api_key_service import create_api_key, revoke_api_key


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, user=None, key=None):
        self._user = user
        self._key = key
        self.added = []

    def query(self, model):
        return _FakeQuery(self._key if model is ApiKey else self._user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _svc(is_service=True):
    return User(id=1, username="svc", is_active=True, is_service_account=is_service)


def test_create_returns_plaintext_and_row():
    db = _FakeDB(user=_svc())
    row, plaintext = create_api_key(db, service_account_username="svc", name="k")
    assert plaintext.startswith("ahk_")
    assert row.name == "k"
    assert row.key_hash and row.key_prefix
    assert row in db.added


def test_create_rejects_non_service_account():
    db = _FakeDB(user=_svc(is_service=False))
    with pytest.raises(ValueError, match="not a service account"):
        create_api_key(db, service_account_username="alice", name="k")


def test_create_rejects_unknown_account():
    db = _FakeDB(user=None)
    with pytest.raises(ValueError, match="not found"):
        create_api_key(db, service_account_username="ghost", name="k")


def test_revoke_disables_key():
    key = ApiKey(id=5, user_id=1, name="k", key_prefix="ahk_x", key_hash="h", enabled=True)
    row = revoke_api_key(_FakeDB(key=key), 5)
    assert row.enabled is False
    assert row.revoked_at is not None


def test_revoke_unknown_raises():
    with pytest.raises(ValueError, match="not found"):
        revoke_api_key(_FakeDB(key=None), 999)
