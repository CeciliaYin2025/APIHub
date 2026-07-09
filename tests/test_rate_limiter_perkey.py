"""RateLimiterMiddleware keys on api_key principal and honors per-key limit."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.auth_context import AuthContextMiddleware
import app.middleware.auth_context as ac
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.models.user import User
from app.services.auth_service import Principal


def _app_with_key(monkeypatch, rate_limit):
    application = FastAPI()
    # inner: rate limiter; outer: auth context (added last = outer)
    application.add_middleware(RateLimiterMiddleware, max_requests=1000)
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        return {"ok": True}

    user = User(username="svc", is_active=True)
    monkeypatch.setattr(
        ac, "build_principal", lambda request: Principal(type="api_key", user=user, key_id=7, rate_limit=rate_limit)
    )
    return application


def test_per_key_limit_enforced(monkeypatch):
    client = TestClient(_app_with_key(monkeypatch, rate_limit=2))
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 429


def test_null_key_limit_falls_back_to_global(monkeypatch):
    client = TestClient(_app_with_key(monkeypatch, rate_limit=None))
    # global max_requests=1000 → 3 quick calls all pass
    for _ in range(3):
        assert client.get("/probe").status_code == 200


def test_no_principal_keys_on_ip_uses_global_limit(monkeypatch):
    """When principal is None, middleware keys on client IP and uses global limit."""
    application = FastAPI()
    application.add_middleware(RateLimiterMiddleware, max_requests=2)
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        return {"ok": True}

    # Monkeypatch build_principal to return None (no principal → IP-keyed fallback)
    monkeypatch.setattr(ac, "build_principal", lambda request: None)

    client = TestClient(application)
    # First 2 requests within global limit=2 pass
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    # 3rd request exceeds limit → 429
    assert client.get("/probe").status_code == 429


def test_explicit_zero_rate_limit_blocks_immediately(monkeypatch):
    """An explicit rate_limit=0 should block the first request."""
    client = TestClient(_app_with_key(monkeypatch, rate_limit=0))
    # rate_limit=0 means no requests allowed → first request blocked
    assert client.get("/probe").status_code == 429
