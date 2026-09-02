"""HTTP transport: health route and credential passthrough middleware."""

import asyncio
import contextlib

import httpx
import pytest

from mcp_server.http_app import CredentialPassthroughMiddleware, credential_fingerprint, passthrough_headers


def _app_with_backend(monkeypatch, backend):
    """create_http_app() wired to a mock REST backend instead of a live one.

    The health route now reaches upstream, so leaving the real client in place
    would make this suite pass or fail depending on whether something happens to
    be listening on localhost:8000.
    """
    import mcp_server.http_app as http_app_mod
    from mcp_server.client import ApiHubClient

    def patched_client(base_url, **kwargs):
        kwargs["transport"] = httpx.MockTransport(backend)
        return ApiHubClient(base_url, **kwargs)

    monkeypatch.setattr(http_app_mod, "ApiHubClient", patched_client)
    return http_app_mod.create_http_app()


@pytest.mark.asyncio
async def test_health_reports_ok_when_the_api_hub_answers(monkeypatch):
    def backend(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/health"
        return httpx.Response(200, json={"status": "ok"})

    app = _app_with_backend(monkeypatch, backend)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["api_hub"]["status"] == "ok"


@pytest.mark.asyncio
async def test_health_is_503_when_the_api_hub_is_unreachable(monkeypatch):
    """The whole point: this process is a proxy, so alive-but-cut-off is not healthy."""

    def backend(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app = _app_with_backend(monkeypatch, backend)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert "unreachable" in resp.json()["api_hub"]["error"].lower()


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


def test_transport_security_derived_from_public_url(monkeypatch):
    from mcp_server.config import mcp_settings
    from mcp_server.http_app import transport_security

    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "allowed_hosts", "")
    monkeypatch.setattr(mcp_settings, "allowed_origins", "")
    settings = transport_security()
    assert settings.enable_dns_rebinding_protection is True
    assert "isap.example.com" in settings.allowed_hosts
    assert "https://isap.example.com" in settings.allowed_origins


def test_transport_security_explicit_settings_win(monkeypatch):
    from mcp_server.config import mcp_settings
    from mcp_server.http_app import transport_security

    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "allowed_hosts", "a.internal, b.internal")
    monkeypatch.setattr(mcp_settings, "allowed_origins", "https://a.internal")
    settings = transport_security()
    assert settings.allowed_hosts == ["a.internal", "b.internal"]
    assert settings.allowed_origins == ["https://a.internal"]


def test_transport_security_off_when_nothing_configured(monkeypatch):
    """Local development: no public URL to derive from, so don't brick the endpoint."""
    from mcp_server.config import mcp_settings
    from mcp_server.http_app import transport_security

    monkeypatch.setattr(mcp_settings, "public_url", "")
    monkeypatch.setattr(mcp_settings, "allowed_hosts", "")
    monkeypatch.setattr(mcp_settings, "allowed_origins", "")
    assert transport_security() is None


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
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            return httpx.Response(200, json=[])
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
    # the result carries machine-readable structured content, not just a text blob
    assert '"structuredContent"' in resp.text
    assert '"databases"' in resp.text


@pytest.mark.asyncio
async def test_two_callers_do_not_share_a_tool_catalog(monkeypatch):
    """The real leak: caller A's RBAC decides what caller B sees for the whole TTL."""
    import mcp_server.http_app as http_app_mod
    from mcp_server.client import ApiHubClient
    from mcp_server.config import mcp_settings

    monkeypatch.setattr(mcp_settings, "public_url", "")
    monkeypatch.setattr(mcp_settings, "db_aliases", "pg_test")

    def backend(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            caller = request.headers.get("X-API-Key")
            visible = [{"name": "everyone_report", "description": "d", "params": {}}]
            if caller == "sk-privileged":
                visible.append({"name": "restricted_report", "description": "d", "params": {}})
            return httpx.Response(200, json=visible)
        return httpx.Response(404, json={"detail": "nope"})

    monkeypatch.setattr(
        http_app_mod,
        "ApiHubClient",
        lambda base_url, **kw: ApiHubClient(base_url, **{**kw, "transport": httpx.MockTransport(backend)}),
    )
    app = http_app_mod.create_http_app()

    async def list_tools_as(api_key):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            resp = await client.post(
                "/mcp/",
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        return resp.text

    async with _lifespan(app):
        privileged = await list_tools_as("sk-privileged")
        limited = await list_tools_as("sk-limited")

    assert "query_pg_test_restricted_report" in privileged
    assert "query_pg_test_restricted_report" not in limited
    assert "query_pg_test_everyone_report" in limited


@pytest.mark.asyncio
async def test_unknown_tool_is_a_json_rpc_protocol_error(monkeypatch):
    """The spec classifies an unknown tool as a protocol error (-32602), not as a
    tool execution error the model should try to recover from."""
    import mcp_server.http_app as http_app_mod
    from mcp_server.client import ApiHubClient
    from mcp_server.config import mcp_settings

    monkeypatch.setattr(mcp_settings, "public_url", "")
    monkeypatch.setattr(mcp_settings, "db_aliases", "pg_test")

    def backend(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "nope"})

    monkeypatch.setattr(
        http_app_mod,
        "ApiHubClient",
        lambda base_url, **kw: ApiHubClient(base_url, **{**kw, "transport": httpx.MockTransport(backend)}),
    )
    app = http_app_mod.create_http_app()

    transport = httpx.ASGITransport(app=app)
    async with _lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            resp = await client.post(
                "/mcp/",
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "query_pg_test_no_such_thing", "arguments": {}},
                },
            )
    assert resp.status_code == 200
    assert '"error"' in resp.text
    assert '"code":-32602' in resp.text
    assert '"isError"' not in resp.text  # not a result at all


@pytest.mark.asyncio
async def test_real_query_envelope_satisfies_the_declared_output_schema(monkeypatch):
    """The SDK validates every result against outputSchema and fails the call on a
    mismatch, so an over-tight schema would break queries at runtime, not at build."""
    import mcp_server.http_app as http_app_mod
    from mcp_server.client import ApiHubClient
    from mcp_server.config import mcp_settings

    monkeypatch.setattr(mcp_settings, "public_url", "")
    monkeypatch.setattr(mcp_settings, "db_aliases", "pg_test")

    def backend(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            return httpx.Response(
                200,
                json=[{"name": "order_summary", "description": "d", "params": {}}],
            )
        if request.url.path == "/api/v1/pg_test/queries/execute":
            return httpx.Response(
                200,
                json={  # verbatim app/schemas/query.py::QueryResponse for fetch_mode=page
                    "success": True,
                    "query_name": "order_summary",
                    "fetch_mode": "page",
                    "total": 2,
                    "page": 1,
                    "page_size": 50,
                    "batch_offset": None,
                    "batch_size": None,
                    "has_more": None,
                    "columns": ["ID", "AMOUNT"],
                    "data": [{"ID": 1, "AMOUNT": 10.5}, {"ID": 2, "AMOUNT": None}],
                },
            )
        return httpx.Response(404, json={"detail": "nope"})

    monkeypatch.setattr(
        http_app_mod,
        "ApiHubClient",
        lambda base_url, **kw: ApiHubClient(base_url, **{**kw, "transport": httpx.MockTransport(backend)}),
    )
    app = http_app_mod.create_http_app()

    transport = httpx.ASGITransport(app=app)
    async with _lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            resp = await client.post(
                "/mcp/",
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "query_pg_test_order_summary", "arguments": {}},
                },
            )
    assert resp.status_code == 200
    assert "Output validation error" not in resp.text
    assert '"isError":false' in resp.text
    assert '"structuredContent"' in resp.text


@pytest.mark.asyncio
async def test_refresh_tool_emits_a_list_changed_notification(monkeypatch):
    """Declaring listChanged is only half of it — the notification has to arrive,
    or a client that cached tools/list never learns about a new definition."""
    import mcp_server.http_app as http_app_mod
    from mcp_server.client import ApiHubClient
    from mcp_server.config import mcp_settings

    monkeypatch.setattr(mcp_settings, "public_url", "")
    monkeypatch.setattr(mcp_settings, "db_aliases", "pg_test")

    # A definition is added between the catalog load the SDK does to validate the
    # incoming call and the reload that refresh_query_tools performs — i.e. exactly
    # the "someone just registered a new query" case the tool exists for.
    served = {"count": 0}

    def backend(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            served["count"] += 1
            defs = [{"name": "order_summary", "description": "d", "params": {}}]
            if served["count"] > 1:
                defs.append({"name": "brand_new_report", "description": "d", "params": {}})
            return httpx.Response(200, json=defs)
        return httpx.Response(404, json={"detail": "nope"})

    monkeypatch.setattr(
        http_app_mod,
        "ApiHubClient",
        lambda base_url, **kw: ApiHubClient(base_url, **{**kw, "transport": httpx.MockTransport(backend)}),
    )
    app = http_app_mod.create_http_app()

    transport = httpx.ASGITransport(app=app)
    async with _lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            resp = await client.post(
                "/mcp/",
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "refresh_query_tools", "arguments": {}},
                },
            )
    assert resp.status_code == 200
    assert "notifications/tools/list_changed" in resp.text


def _guarded_app(monkeypatch):
    """A real app with DNS-rebinding protection derived from a public URL."""
    import mcp_server.http_app as http_app_mod
    from mcp_server.client import ApiHubClient
    from mcp_server.config import mcp_settings

    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "allowed_hosts", "")
    monkeypatch.setattr(mcp_settings, "allowed_origins", "")

    def backend(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json=[{"alias": "pg_test", "db_type": "postgresql", "description": "t"}])
        return httpx.Response(404, json={"detail": "nope"})

    monkeypatch.setattr(
        http_app_mod,
        "ApiHubClient",
        lambda base_url, **kw: ApiHubClient(base_url, **{**kw, "transport": httpx.MockTransport(backend)}),
    )
    return http_app_mod.create_http_app()


async def _post_mcp(app, headers):
    transport = httpx.ASGITransport(app=app)
    async with _lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://isap.example.com") as client:
            return await client.post(
                "/mcp/",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    **headers,
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_databases"}},
            )


@pytest.mark.asyncio
async def test_cross_origin_request_is_rejected(monkeypatch):
    """DNS rebinding: a browser on another site must not reach the MCP endpoint."""
    resp = await _post_mcp(_guarded_app(monkeypatch), {"Origin": "https://evil.example.com"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_same_origin_request_is_accepted(monkeypatch):
    resp = await _post_mcp(_guarded_app(monkeypatch), {"Origin": "https://isap.example.com"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_without_origin_header_is_accepted(monkeypatch):
    """Non-browser MCP clients send no Origin — they must keep working."""
    resp = await _post_mcp(_guarded_app(monkeypatch), {})
    assert resp.status_code == 200


def test_credential_fingerprint_separates_callers_without_holding_the_token():
    from mcp_server.http_app import _request_headers

    token = _request_headers.set({"Authorization": "Bearer secret-a"})
    try:
        a = credential_fingerprint()
        _request_headers.set({"Authorization": "Bearer secret-b"})
        b = credential_fingerprint()
        _request_headers.set({"authorization": "Bearer secret-a"})
        a_again = credential_fingerprint()
    finally:
        _request_headers.reset(token)

    assert a != b
    assert a == a_again  # header-name casing is not a second caller
    assert "secret" not in a
    assert credential_fingerprint() != a  # anonymous is its own key
