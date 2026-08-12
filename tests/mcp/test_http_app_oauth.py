"""OAuth gate: 401 challenge, metadata route, JWT injection on success."""

import httpx
import pytest

from mcp_server.config import mcp_settings
from mcp_server.http_app import OAuthGateMiddleware, create_http_app, passthrough_headers


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(mcp_settings, "oauth_enabled", True)
    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "auth_issuer", "https://isap.example.com/auth")


class _FakeExchanger:
    def __init__(self, result):
        self._result = result

    async def exchange(self, token):
        return self._result if token == "good" else None


def _inner_capture(captured):
    async def inner_app(scope, receive, send):
        captured["headers"] = passthrough_headers()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return inner_app


@pytest.mark.asyncio
async def test_missing_bearer_gets_401_challenge():
    app = OAuthGateMiddleware(_inner_capture({}), _FakeExchanger("jwt"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp")
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers["WWW-Authenticate"]


@pytest.mark.asyncio
async def test_invalid_bearer_gets_401():
    app = OAuthGateMiddleware(_inner_capture({}), _FakeExchanger("jwt"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_valid_bearer_injects_local_jwt():
    captured = {}
    app = OAuthGateMiddleware(_inner_capture(captured), _FakeExchanger("local-jwt"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert captured["headers"] == {"Authorization": "Bearer local-jwt"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/anything"])
async def test_gate_fires_behind_a_root_path(path):
    """Deployment runs with --root-path, and uvicorn prepends it to scope["path"].

    A request for /mcp/ then arrives as /apihub/mcp/mcp/. Matching the raw path
    against "/mcp" never fired, so the gate was skipped and every MCP call ran
    unauthenticated (failing closed downstream, but wholly unusable).
    """
    root = "/apihub/mcp"
    captured = {}
    app = OAuthGateMiddleware(_inner_capture(captured), _FakeExchanger("jwt"))
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": root + path,
            "root_path": root,
            "headers": [],
            "query_string": b"",
        },
        receive,
        send,
    )

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 401, "OAuth gate must still guard /mcp when mounted under a root_path"
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert "resource_metadata=" in headers["www-authenticate"]
    assert captured == {}, "request must never reach the inner app unauthenticated"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/.well-known/oauth-protected-resource"])
async def test_public_routes_stay_public_behind_a_root_path(path):
    """Stripping root_path must not accidentally gate the unauthenticated routes."""
    root = "/apihub/mcp"
    captured = {}
    app = OAuthGateMiddleware(_inner_capture(captured), _FakeExchanger("jwt"))
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "GET",
            "path": root + path,
            "root_path": root,
            "headers": [],
            "query_string": b"",
        },
        receive,
        send,
    )

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    assert captured["headers"] == {}


@pytest.mark.asyncio
async def test_wellknown_route_is_public():
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert resp.json()["authorization_servers"] == ["https://isap.example.com/auth"]


@pytest.mark.asyncio
async def test_health_stays_public_under_oauth():
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
