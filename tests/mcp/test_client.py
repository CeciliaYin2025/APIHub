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


def test_static_headers_empty_key_yields_no_header():
    assert static_headers("")() == {}
    assert static_headers("sk-x")() == {"X-API-Key": "sk-x"}
