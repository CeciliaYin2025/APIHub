"""HTTP transport: health route and credential passthrough middleware."""

import httpx
import pytest

from mcp_server.http_app import CredentialPassthroughMiddleware, create_http_app, passthrough_headers


@pytest.mark.asyncio
async def test_health_endpoint():
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_passthrough_middleware_captures_api_key_header():
    captured = {}

    async def inner_app(scope, receive, send):
        captured["headers"] = passthrough_headers()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = CredentialPassthroughMiddleware(inner_app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        await client.get("/anything", headers={"X-API-Key": "sk-abc"})
    assert captured["headers"] == {"X-API-Key": "sk-abc"}


@pytest.mark.asyncio
async def test_passthrough_middleware_prefers_api_key_falls_back_to_bearer():
    captured = {}

    async def inner_app(scope, receive, send):
        captured["headers"] = passthrough_headers()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = CredentialPassthroughMiddleware(inner_app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        await client.get("/x", headers={"Authorization": "Bearer sk-tok"})
        assert captured["headers"] == {"Authorization": "Bearer sk-tok"}
        await client.get("/x")
        assert captured["headers"] == {}
