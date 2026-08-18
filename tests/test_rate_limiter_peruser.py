"""RateLimiterMiddleware gives every user principal its own window.

Behind the ISAP gateway ``request.client.host`` is the gateway for every
caller, so keying user traffic on the IP put the whole company in one shared
60/min bucket. These tests pin the per-username split that replaces it.
"""

import app.middleware.auth_context as ac
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.models.user import User
from app.services.auth_service import Principal


def _app(monkeypatch, principal_for, max_requests=2):
    application = FastAPI()
    application.add_middleware(RateLimiterMiddleware, max_requests=max_requests)
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        return {"ok": True}

    monkeypatch.setattr(ac, "build_principal", principal_for)
    return application


def _user_principal(username, ptype="user"):
    return Principal(type=ptype, user=User(username=username, is_active=True))


def test_two_users_on_one_ip_do_not_share_a_bucket(monkeypatch):
    """Same client IP, different usernames — one must not exhaust the other."""
    seq = ["alice", "alice", "bob", "bob"]
    calls = iter(seq)
    client = TestClient(_app(monkeypatch, lambda request: _user_principal(next(calls))))

    for _ in range(len(seq)):
        assert client.get("/probe").status_code == 200


def test_single_user_still_limited(monkeypatch):
    client = TestClient(_app(monkeypatch, lambda request: _user_principal("alice")))
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 429


def test_sso_principal_keyed_like_a_user(monkeypatch):
    """An OAuth/SSO caller is a user, not an api_key — same per-username rule."""
    client = TestClient(_app(monkeypatch, lambda request: _user_principal("carol", ptype="sso_user")))
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 429
