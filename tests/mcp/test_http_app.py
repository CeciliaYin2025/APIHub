"""HTTP transport: health route and credential passthrough middleware."""

import asyncio
import contextlib

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


@contextlib.asynccontextmanager
async def _lifespan(app):
    """Drive the ASGI lifespan protocol manually (httpx.ASGITransport does not)."""
    receive_queue: asyncio.Queue = asyncio.Queue()
    startup = asyncio.get_running_loop().create_future()
    shutdown = asyncio.get_running_loop().create_future()

    async def send(message):
        if message["type"] == "lifespan.startup.complete":
            startup.set_result(None)
        elif message["type"] == "lifespan.startup.failed":
            startup.set_exception(RuntimeError(message.get("message", "lifespan startup failed")))
        elif message["type"] == "lifespan.shutdown.complete":
            shutdown.set_result(None)
        elif message["type"] == "lifespan.shutdown.failed":
            shutdown.set_exception(RuntimeError(message.get("message", "lifespan shutdown failed")))

    task = asyncio.create_task(app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive_queue.get, send))
    await receive_queue.put({"type": "lifespan.startup"})
    await startup
    try:
        yield
    finally:
        await receive_queue.put({"type": "lifespan.shutdown"})
        await shutdown
        await task


@pytest.mark.asyncio
async def test_mcp_tool_call_forwards_caller_credential(monkeypatch):
    """End-to-end through the real mounted /mcp: StreamableHTTPSessionManager(stateless)
    runs the tool handler inside the HTTP request's task context, so the contextvar
    credential must reach the proxied REST call."""
    import mcp_server.http_app as http_app_mod
    from mcp_server.client import ApiHubClient

    seen_keys: list[str | None] = []

    def backend(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers.get("X-API-Key"))
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json=[{"alias": "pg_test", "db_type": "postgresql", "description": "t"}])
        return httpx.Response(404, json={"detail": "nope"})

    def patched_client(base_url, **kwargs):
        kwargs["transport"] = httpx.MockTransport(backend)
        return ApiHubClient(base_url, **kwargs)

    monkeypatch.setattr(http_app_mod, "ApiHubClient", patched_client)
    app = http_app_mod.create_http_app()

    transport = httpx.ASGITransport(app=app)
    async with _lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            resp = await client.post(
                "/mcp/",  # Starlette Mount 307-redirects the bare "/mcp"
                headers={
                    "X-API-Key": "sk-live-test",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "list_databases", "arguments": {}},
                },
            )
    assert resp.status_code == 200
    assert "sk-live-test" in seen_keys
