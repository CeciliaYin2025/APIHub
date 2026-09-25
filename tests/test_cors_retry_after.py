"""A browser client can see a 429 and read its Retry-After.

CORS is the outermost middleware, so the limiters' own 429s still pass through it
and get Access-Control-Allow-Origin. Retry-After is not a CORS-safelisted header,
though: without Access-Control-Expose-Headers the page cannot read how long to wait.
"""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import app.middleware.auth_context as ac
from app.main import app as real_app
from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.concurrency_limiter import ConcurrencyLimiterMiddleware
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.models.user import User
from app.services.auth_service import Principal

ORIGIN = "https://portal.example.com"


def _real_cors_kwargs():
    """The CORS options app.main actually ships, so the test cannot drift from them."""
    (mw,) = [m for m in real_app.user_middleware if m.cls is CORSMiddleware]
    return mw.kwargs


def _build(monkeypatch, limiter, hold=0.0, **limiter_kwargs):
    application = FastAPI()
    application.add_middleware(limiter, **limiter_kwargs)
    application.add_middleware(AuthContextMiddleware)
    application.add_middleware(CORSMiddleware, **_real_cors_kwargs())

    @application.get("/probe")
    async def probe(request: Request):
        await asyncio.sleep(hold)
        return {"ok": True}

    principal = Principal(type="sso_user", user=User(username="00049", is_active=True))
    monkeypatch.setattr(ac, "build_principal", lambda request: principal)
    return application


def _assert_browser_readable_429(resp):
    assert resp.status_code == 429
    assert resp.headers["access-control-allow-origin"] in ("*", ORIGIN)
    exposed = [h.strip().lower() for h in resp.headers["access-control-expose-headers"].split(",")]
    assert "retry-after" in exposed
    assert int(resp.headers["retry-after"]) >= 1


def test_rate_limited_429_is_readable_cross_origin(monkeypatch):
    client = TestClient(_build(monkeypatch, RateLimiterMiddleware, max_requests=2))
    for _ in range(2):
        assert client.get("/probe", headers={"Origin": ORIGIN}).status_code == 200

    resp = client.get("/probe", headers={"Origin": ORIGIN})

    _assert_browser_readable_429(resp)
    assert int(resp.headers["retry-after"]) <= 60


@pytest.mark.asyncio
async def test_concurrency_limited_429_is_readable_cross_origin(monkeypatch):
    application = _build(monkeypatch, ConcurrencyLimiterMiddleware, hold=0.2, max_concurrent=1, wait_seconds=0.0)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        results = await asyncio.gather(*[client.get("/probe", headers={"Origin": ORIGIN}) for _ in range(3)])

    rejected = [r for r in results if r.status_code == 429]
    assert rejected
    for resp in rejected:
        _assert_browser_readable_429(resp)
