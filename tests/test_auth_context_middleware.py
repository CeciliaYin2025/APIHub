"""AuthContextMiddleware sets request.state.principal and short-circuits ApiKeyError."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import app.middleware.auth_context as ac
from app.middleware.auth_context import AuthContextMiddleware
from app.models.user import User
from app.services.auth_service import ApiKeyError, Principal


def _make_app():
    application = FastAPI()
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        p = request.state.principal
        return {"who": None if p is None else p.user.username}

    return application


def test_principal_set_when_resolved(monkeypatch):
    user = User(username="svc", is_active=True)
    monkeypatch.setattr(ac, "build_principal", lambda request: Principal(type="api_key", user=user, key_id=1))
    client = TestClient(_make_app())
    assert client.get("/probe").json() == {"who": "svc"}


def test_principal_none_when_unresolved(monkeypatch):
    monkeypatch.setattr(ac, "build_principal", lambda request: None)
    client = TestClient(_make_app())
    assert client.get("/probe").json() == {"who": None}


def test_apikey_error_short_circuits(monkeypatch):
    def _raise(request):
        raise ApiKeyError(403, "Source IP not allowed")

    monkeypatch.setattr(ac, "build_principal", _raise)
    client = TestClient(_make_app())
    resp = client.get("/probe")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Source IP not allowed"
