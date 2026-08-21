"""ApiHubClient proxies REST calls with injected auth headers and maps errors."""

import httpx
import pytest

from mcp_server.client import ApiHubClient, ApiHubError, static_headers


def make_client(handler, headers_provider=None):
    return ApiHubClient(
        "http://hub",
        headers_provider=headers_provider,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_execute_query_posts_payload_with_api_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("X-API-Key")
        seen["body"] = request.content
        return httpx.Response(200, json={"success": True, "total": 0, "columns": [], "data": []})

    client = make_client(handler, headers_provider=static_headers("sk-test"))
    result = await client.execute_query("pg_test", "order_summary", {"status": "OPEN"}, page=2, page_size=10)

    assert result["success"] is True
    assert seen["url"] == "http://hub/api/v1/pg_test/queries/execute"
    assert seen["api_key"] == "sk-test"
    import json

    body = json.loads(seen["body"])
    assert body == {
        "query_name": "order_summary",
        "params": {"status": "OPEN"},
        "fetch_mode": "page",
        "page": 2,
        "page_size": 10,
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_execute_query_forwards_a_non_default_fetch_mode():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"success": True, "total": 7, "columns": [], "data": []})

    client = make_client(handler)
    await client.execute_query("pg_test", "order_summary", {}, fetch_mode="count")
    import json

    assert json.loads(seen["body"])["fetch_mode"] == "count"
    await client.aclose()


@pytest.mark.asyncio
async def test_catalog_version_unwraps_the_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/mcp/catalog-version"
        return httpx.Response(200, json={"version": "abc123"})

    client = make_client(handler)
    assert await client.catalog_version() == "abc123"
    await client.aclose()


@pytest.mark.asyncio
async def test_ping_hits_health_and_raises_when_unreachable():
    def ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/health"
        return httpx.Response(200, json={"status": "ok"})

    client = make_client(ok)
    await client.ping()  # does not raise
    await client.aclose()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = make_client(down)
    with pytest.raises(ApiHubError):
        await client.ping()
    await client.aclose()


@pytest.mark.asyncio
async def test_list_definitions_and_databases_paths():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[])

    client = make_client(handler)
    await client.list_databases()
    await client.list_definitions("orcl")
    await client.describe_table("orcl", "HR.EMPLOYEES")
    assert calls == [
        "http://hub/api/v1/databases",
        # permitted_only keeps the tool catalog — which is fed straight into an
        # LLM's context — down to what this credential could actually execute.
        "http://hub/api/v1/orcl/queries/definitions?permitted_only=true",
        "http://hub/api/v1/orcl/schemas/describe/HR.EMPLOYEES",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_http_error_maps_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Permission denied: read on query:orders"})

    client = make_client(handler)
    with pytest.raises(ApiHubError) as exc:
        await client.execute_query("orcl", "orders", {})
    assert exc.value.status_code == 403
    assert "Permission denied" in exc.value.detail
    await client.aclose()


@pytest.mark.asyncio
async def test_connection_error_maps_to_apihub_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = make_client(handler)
    with pytest.raises(ApiHubError) as exc:
        await client.list_databases()
    assert exc.value.status_code == 0
    assert "unreachable" in exc.value.detail.lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_describe_table_rejects_path_traversal():
    """A model-supplied table name must not be able to steer the proxy elsewhere."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    client = make_client(handler)
    with pytest.raises(ApiHubError) as exc:
        await client.describe_table("orcl", "../../../auth/users")
    assert exc.value.status_code == 400
    assert calls == []  # rejected before any HTTP request went out
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_db_alias_rejected_on_every_path_building_call():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    client = make_client(handler)
    for call in (
        lambda: client.list_definitions("../databases"),
        lambda: client.describe_table("../../auth/users", "orders"),
        lambda: client.execute_query("orcl/../..", "orders", {}),
    ):
        with pytest.raises(ApiHubError) as exc:
            await call()
        assert exc.value.status_code == 400
    assert calls == []
    await client.aclose()


@pytest.mark.asyncio
async def test_schema_qualified_and_oracle_identifier_chars_still_allowed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    client = make_client(handler)
    await client.describe_table("orcl", "HR.EMPLOYEES")
    await client.describe_table("orcl", "SYS$AUDIT#LOG")
    assert calls == [
        "/api/v1/orcl/schemas/describe/HR.EMPLOYEES",
        "/api/v1/orcl/schemas/describe/SYS$AUDIT#LOG",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_server_error_detail_is_not_forwarded_to_the_model():
    """5xx bodies carry driver/schema internals; a 4xx detail is actionable and stays."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "ORA-00942: table or view does not exist [SCHEMA.SECRET_TABLE]"})

    client = make_client(handler)
    with pytest.raises(ApiHubError) as exc:
        await client.execute_query("orcl", "orders", {})
    assert exc.value.status_code == 500
    assert "ORA-00942" not in exc.value.detail
    assert "SECRET_TABLE" not in exc.value.detail
    await client.aclose()


def test_request_timeout_is_configurable():
    client = ApiHubClient("http://hub", timeout=5.0)
    assert client._http.timeout.read == 5.0


def test_static_headers_empty_key_yields_no_header():
    assert static_headers("")() == {}
    assert static_headers("sk-x")() == {"X-API-Key": "sk-x"}
