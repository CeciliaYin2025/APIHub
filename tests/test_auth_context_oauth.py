"""AuthContextMiddleware's OAuth branch: isap-auth bearer -> local principal.

The introspection call itself is covered by tests/test_oauth_rs.py; here it is
stubbed so these tests pin the wiring — when the branch runs, what it produces,
and how it fails.
"""

import app.middleware.auth_context as ac
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.core.config import settings
from app.middleware.auth_context import AuthContextMiddleware
from app.models.user import User
from app.services.auth_service import Principal


@pytest.fixture(autouse=True)
def _oauth_on(monkeypatch):
    monkeypatch.setattr(settings, "oauth_rs_enabled", True)
    monkeypatch.setattr(settings, "oauth_introspection_url", "http://isap-auth:9997/auth/api/v1/introspect")
    monkeypatch.setattr(settings, "apihub_public_url", "https://isap.example.com/apihub")
    # Nothing local ever resolves, so every test exercises the OAuth branch.
    monkeypatch.setattr(ac, "build_principal", lambda request: None)


def _app():
    application = FastAPI()
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        p = request.state.principal
        return {"who": None if p is None else p.user.username, "type": None if p is None else p.type}

    return TestClient(application)


def _introspect(monkeypatch, claims, calls=None):
    async def _fake(token):
        if calls is not None:
            calls.append(token)
        return claims

    monkeypatch.setattr(ac.oauth_rs, "introspect", _fake)


def _sso_user(monkeypatch, username="alice"):
    seen = {}

    def _resolve(db, name, email):
        seen["username"], seen["email"] = name, email
        return User(username=username, is_active=True)

    monkeypatch.setattr(ac, "resolve_sso_user", _resolve)
    return seen


def test_valid_token_resolves_to_sso_principal(monkeypatch):
    _introspect(monkeypatch, {"active": True, "username": "alice", "email": "alice@example.com"})
    seen = _sso_user(monkeypatch)

    body = _app().get("/probe", headers={"Authorization": "Bearer opaque-token"}).json()

    assert body == {"who": "alice", "type": "sso_user"}
    assert seen == {"username": "alice", "email": "alice@example.com"}


def test_sub_used_when_username_claim_absent(monkeypatch):
    _introspect(monkeypatch, {"active": True, "sub": "bob"})
    seen = _sso_user(monkeypatch, username="bob")

    assert _app().get("/probe", headers={"Authorization": "Bearer t"}).json()["who"] == "bob"
    assert seen["username"] == "bob"


def test_invalid_token_leaves_principal_unresolved(monkeypatch):
    _introspect(monkeypatch, None)
    assert _app().get("/probe", headers={"Authorization": "Bearer dead"}).json() == {"who": None, "type": None}


def test_claims_without_identity_rejected(monkeypatch):
    """An active token that names nobody must not resolve to a user."""
    _introspect(monkeypatch, {"active": True})
    _sso_user(monkeypatch)
    assert _app().get("/probe", headers={"Authorization": "Bearer t"}).json()["who"] is None


def test_unprovisioned_user_gets_403_with_challenge(monkeypatch):
    _introspect(monkeypatch, {"active": True, "username": "stranger"})

    def _reject(db, name, email):
        raise HTTPException(status_code=403, detail="User not provisioned")

    monkeypatch.setattr(ac, "resolve_sso_user", _reject)

    resp = _app().get("/probe", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "User not provisioned"}
    assert "oauth-protected-resource" in resp.headers["WWW-Authenticate"]


def test_branch_skipped_when_disabled(monkeypatch):
    """With OAUTH_RS_ENABLED off, no token ever reaches the authorization server."""
    monkeypatch.setattr(settings, "oauth_rs_enabled", False)
    calls = []
    _introspect(monkeypatch, {"active": True, "username": "alice"}, calls)
    _sso_user(monkeypatch)

    assert _app().get("/probe", headers={"Authorization": "Bearer t"}).json()["who"] is None
    assert calls == []


def test_api_key_bearer_never_introspected(monkeypatch):
    """`Bearer ahk_...` is an API key. build_principal already rejected it, and
    re-offering it to isap-auth would leak the key to another service."""
    calls = []
    _introspect(monkeypatch, {"active": True, "username": "alice"}, calls)

    assert _app().get("/probe", headers={"Authorization": "Bearer ahk_secret"}).json()["who"] is None
    assert calls == []


def test_no_credential_makes_no_call(monkeypatch):
    calls = []
    _introspect(monkeypatch, {"active": True, "username": "alice"}, calls)

    assert _app().get("/probe").json() == {"who": None, "type": None}
    assert calls == []


def test_local_principal_short_circuits_introspection(monkeypatch):
    """A password JWT or API key must not cost an introspection round-trip."""
    monkeypatch.setattr(
        ac, "build_principal", lambda request: Principal(type="user", user=User(username="local", is_active=True))
    )
    calls = []
    _introspect(monkeypatch, {"active": True, "username": "alice"}, calls)

    assert _app().get("/probe", headers={"Authorization": "Bearer local-jwt"}).json() == {
        "who": "local",
        "type": "user",
    }
    assert calls == []
