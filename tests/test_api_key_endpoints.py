"""Route-level tests for API key endpoints and service-account rules."""

import pytest
from fastapi import HTTPException

from app.api.v1.auth import (
    create_api_key_endpoint,
    create_user,
    revoke_api_key_endpoint,
)
from app.models.user import ApiKey, User
from app.schemas.api_key import ApiKeyCreate
from app.schemas.auth import UserCreate


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result

    def all(self):
        return self._result if isinstance(self._result, list) else []


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


def _admin():
    return User(username="admin", is_superuser=True, is_active=True)


def _svc():
    return User(id=1, username="svc", is_active=True, is_service_account=True)


def test_create_api_key_returns_plaintext_once():
    db = _FakeDB(user=_svc())
    payload = ApiKeyCreate(service_account_username="svc", name="prod")
    result = create_api_key_endpoint(payload, db=db, admin=_admin())
    # endpoint returns a plain dict (response_model serialization only happens
    # through the app, not on a direct function call)
    assert result["api_key"].startswith("ahk_")
    assert result["name"] == "prod"


def test_create_api_key_for_non_service_account_400():
    db = _FakeDB(user=User(id=2, username="alice", is_service_account=False))
    payload = ApiKeyCreate(service_account_username="alice", name="x")
    with pytest.raises(HTTPException) as e:
        create_api_key_endpoint(payload, db=db, admin=_admin())
    assert e.value.status_code == 400


def test_revoke_unknown_key_404():
    with pytest.raises(HTTPException) as e:
        revoke_api_key_endpoint(999, db=_FakeDB(key=None), _admin=_admin())
    assert e.value.status_code == 404


def test_create_user_service_account_cannot_be_superuser():
    db = _FakeDB(user=None)  # username lookup returns None → no existing user
    payload = UserCreate(username="svc", password="x", is_service_account=True, is_superuser=True)
    with pytest.raises(HTTPException) as e:
        create_user(payload, db=db, _admin=_admin())
    assert e.value.status_code == 400
