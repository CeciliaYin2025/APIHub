"""Identity bridge: verified SSO claims -> local JWT. Internal-only, secret-gated."""

import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.v1.auth import sso_token_bridge
from app.core.config import settings
from app.models.user import User
from app.schemas.sso_token import SsoTokenRequest


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
        self._user = obj

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _payload():
    return SsoTokenRequest(username="alice", email="alice@example.com")


def test_disabled_when_secret_unset(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "")
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="anything", db=_FakeDB())
    assert exc.value.status_code == 404


def test_wrong_secret_403(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="wrong", db=_FakeDB())
    assert exc.value.status_code == 403


def test_known_active_user_gets_jwt(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    user = User(username="alice", is_active=True, is_service_account=False)
    resp = sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=_FakeDB(user=user))
    claims = jwt.decode(resp.access_token, settings.secret_key, algorithms=[settings.algorithm])
    assert claims["sub"] == "alice"
    assert resp.token_type == "bearer"


def test_service_account_rejected(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    user = User(username="alice", is_active=True, is_service_account=True)
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=_FakeDB(user=user))
    assert exc.value.status_code == 403


def test_unknown_user_403_when_autocreate_off(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=_FakeDB(user=None))
    assert exc.value.status_code == 403


def test_unknown_user_created_roleless_when_autocreate_on(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", True)
    db = _FakeDB(user=None)
    resp = sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=db)
    assert len(db.added) == 1
    created = db.added[0]
    assert created.username == "alice"
    assert created.is_active is True
    assert created.is_superuser is False
    claims = jwt.decode(resp.access_token, settings.secret_key, algorithms=[settings.algorithm])
    assert claims["sub"] == "alice"


def test_autocreated_user_cannot_password_login(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", True)
    db = _FakeDB(user=None)
    sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=db)
    created = db.added[0]
    assert created.hashed_password  # valid hash, never empty
    from app.services.auth_service import authenticate_user

    # Must return None (generic 401 path), never raise
    assert authenticate_user(db, "alice", "any-password") is None
