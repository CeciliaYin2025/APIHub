"""Dynamic tool catalog: definition → MCP Tool mapping, naming, TTL refresh."""

import httpx
import pytest

from mcp_server.catalog import ToolCatalog, build_tool, sanitize, tool_name
from mcp_server.client import ApiHubClient

DEFINITION = {
    "name": "order_summary",
    "db_alias": "pg_test",
    "description": "Open orders grouped by customer",
    "permission": "query:orders",
    "params": {
        "status": {"type": "string", "required": True, "description": "Order status code"},
        "min_amount": {"type": "number", "required": False, "description": ""},
        "since": {"type": "date", "required": False, "description": "Earliest order date"},
    },
}


def test_sanitize_and_tool_name():
    assert sanitize("HR.Employee Report!") == "hr_employee_report"
    assert tool_name("pg_test", "order_summary") == "query_pg_test_order_summary"
    assert len(tool_name("a" * 40, "b" * 60)) <= 64


def test_build_tool_schema():
    tool = build_tool("pg_test", DEFINITION, page_size_max=100)
    assert tool.name == "query_pg_test_order_summary"
    assert "Open orders grouped by customer" in tool.description
    assert "[pg_test]" in tool.description
    props = tool.inputSchema["properties"]
    assert props["status"] == {"type": "string", "description": "Order status code"}
    assert props["min_amount"] == {"type": "number"}
    assert props["since"]["type"] == "string"
    assert props["since"]["format"] == "date"
    assert props["page"]["type"] == "integer"
    assert props["page_size"]["maximum"] == 100
    assert tool.inputSchema["required"] == ["status"]
    assert tool.inputSchema["additionalProperties"] is False


def _mock_client(definitions_by_db, databases=None, counter=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(request.url.path)
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json=databases or [])
        for db, defs in definitions_by_db.items():
            if request.url.path == f"/api/v1/{db}/queries/definitions":
                return httpx.Response(200, json=defs)
        return httpx.Response(404, json={"detail": "not found"})

    return ApiHubClient("http://hub", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_catalog_loads_all_databases_when_aliases_empty():
    client = _mock_client(
        {"orcl": [DEFINITION | {"db_alias": "orcl"}], "pg_test": [DEFINITION]},
        databases=[{"alias": "orcl"}, {"alias": "pg_test"}],
    )
    catalog = ToolCatalog(client)
    tools = await catalog.tools()
    names = {t.name for t in tools}
    assert names == {"query_orcl_order_summary", "query_pg_test_order_summary"}
    target = catalog.target("query_pg_test_order_summary")
    assert target.db_alias == "pg_test"
    assert target.query_name == "order_summary"
    await client.aclose()


@pytest.mark.asyncio
async def test_catalog_respects_explicit_aliases_and_ttl_cache():
    counter: list[str] = []
    client = _mock_client({"pg_test": [DEFINITION]}, counter=counter)
    catalog = ToolCatalog(client, db_aliases=["pg_test"], ttl_seconds=3600)
    await catalog.tools()
    await catalog.tools()  # within TTL — no extra fetch
    assert counter.count("/api/v1/pg_test/queries/definitions") == 1
    await catalog.tools(force=True)  # forced refresh fetches again
    assert counter.count("/api/v1/pg_test/queries/definitions") == 2
    # explicit aliases → /databases is never called
    assert "/api/v1/databases" not in counter
    await client.aclose()


@pytest.mark.asyncio
async def test_name_collision_first_wins():
    defs = [DEFINITION, DEFINITION | {"name": "order summary"}]  # sanitizes to same name
    client = _mock_client({"pg_test": defs})
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    tools = await catalog.tools()
    assert len(tools) == 1
    assert catalog.target("query_pg_test_order_summary").query_name == "order_summary"
    await client.aclose()
