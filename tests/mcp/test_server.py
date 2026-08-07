"""Tool listing and dispatch logic (transport-independent)."""

import httpx
import pytest

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient, ApiHubError
from mcp_server.server import STATIC_TOOLS, create_server, dispatch_tool, list_all_tools

DEFINITION = {
    "name": "order_summary",
    "db_alias": "pg_test",
    "description": "Open orders",
    "permission": "query:orders",
    "params": {"status": {"type": "string", "required": True, "description": ""}},
}


PAGING_COLLISION_DEFINITION = {
    "name": "report_by_page",
    "db_alias": "pg_test",
    "description": "Report keyed by the source system's own page label",
    "permission": "query:reports",
    "params": {"page": {"type": "string", "required": True, "description": "Source page label"}},
}


def _fixture(seen=None, definitions=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            return httpx.Response(200, json=definitions or [DEFINITION])
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json=[{"alias": "pg_test", "db_type": "postgresql", "description": "t"}])
        if request.url.path == "/api/v1/pg_test/queries/execute":
            return httpx.Response(200, json={"success": True, "total": 1, "columns": ["A"], "data": [{"A": 1}]})
        if request.url.path == "/api/v1/pg_test/schemas/describe/orders":
            return httpx.Response(200, json={"table": "orders", "columns": []})
        return httpx.Response(404, json={"detail": "nope"})

    client = ApiHubClient("http://hub", transport=httpx.MockTransport(handler))
    catalog = ToolCatalog(client, db_aliases=["pg_test"], page_size_max=100)
    return client, catalog


@pytest.mark.asyncio
async def test_list_all_tools_static_plus_dynamic():
    client, catalog = _fixture()
    tools = await list_all_tools(catalog)
    names = [t.name for t in tools]
    assert "list_databases" in names
    assert "describe_table" in names
    assert "refresh_query_tools" in names
    assert "query_pg_test_order_summary" in names
    await client.aclose()


@pytest.mark.asyncio
async def test_dispatch_query_tool_clamps_page_size():
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()
    result = await dispatch_tool(
        client, catalog, "query_pg_test_order_summary", {"status": "OPEN", "page": 1, "page_size": 9999}
    )
    assert result["success"] is True
    import json

    body = json.loads([c for c in seen if c[1].endswith("/execute")][0][2])
    assert body["page_size"] == 100  # clamped to catalog.page_size_max
    assert body["params"] == {"status": "OPEN"}  # paging args not forwarded as bind params
    await client.aclose()


@pytest.mark.asyncio
async def test_dispatch_forwards_a_declared_page_param_as_a_bind():
    """`page` declared by the definition is a bind value, not the paging knob.

    Only the colliding knob is renamed — this definition leaves `page_size` free,
    so that one keeps its plain name.
    """
    seen = []
    client, catalog = _fixture(seen, definitions=[PAGING_COLLISION_DEFINITION])
    await catalog.tools()
    await dispatch_tool(
        client,
        catalog,
        "query_pg_test_report_by_page",
        {"page": "P-01", "_page": 3, "page_size": 25},
    )
    import json

    body = json.loads([c for c in seen if c[1].endswith("/execute")][0][2])
    assert body["params"] == {"page": "P-01"}
    assert body["page"] == 3
    assert body["page_size"] == 25
    await client.aclose()


@pytest.mark.asyncio
async def test_dispatch_static_tools():
    client, catalog = _fixture()
    dbs = await dispatch_tool(client, catalog, "list_databases", {})
    assert dbs["databases"][0]["alias"] == "pg_test"
    desc = await dispatch_tool(client, catalog, "describe_table", {"db": "pg_test", "table": "orders"})
    assert desc["table"] == "orders"
    refreshed = await dispatch_tool(client, catalog, "refresh_query_tools", {})
    assert refreshed["query_tools"] == 1
    assert refreshed["shadowed"] == []
    assert refreshed["truncated"] == []
    assert refreshed["changed"] is True  # first load in this process
    await client.aclose()


async def _dispatch_body(seen):
    import json

    return json.loads([c for c in seen if c[1].endswith("/execute")][-1][2])


@pytest.mark.asyncio
async def test_fetch_mode_defaults_to_page_and_is_not_sent_as_a_bind():
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()
    await dispatch_tool(client, catalog, "query_pg_test_order_summary", {"status": "OPEN"})
    body = await _dispatch_body(seen)
    assert body["fetch_mode"] == "page"
    assert body["params"] == {"status": "OPEN"}
    await client.aclose()


@pytest.mark.asyncio
async def test_count_and_one_are_forwarded():
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()
    for mode in ("count", "one"):
        await dispatch_tool(client, catalog, "query_pg_test_order_summary", {"status": "OPEN", "fetch_mode": mode})
        body = await _dispatch_body(seen)
        assert body["fetch_mode"] == mode
        assert "fetch_mode" not in body["params"]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["all", "batch", "top_n", "nonsense"])
async def test_context_flooding_fetch_modes_are_refused(mode):
    """The enum is advice to the model; this is the boundary that enforces it."""
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()
    with pytest.raises(ApiHubError) as exc:
        await dispatch_tool(client, catalog, "query_pg_test_order_summary", {"fetch_mode": mode})
    assert exc.value.status_code == 400
    # Rejected before any REST call — the API never sees the request.
    assert not [c for c in seen if c[1].endswith("/execute")]


def test_static_tools_carry_annotations_and_titles():
    by_name = {t.name: t for t in STATIC_TOOLS}
    assert by_name["list_databases"].annotations.readOnlyHint is True
    assert by_name["describe_table"].annotations.readOnlyHint is True
    assert by_name["list_databases"].title
    assert by_name["describe_table"].title
    # rebuilding the catalog mutates this process's state, so it is not read-only
    assert by_name["refresh_query_tools"].annotations.readOnlyHint is False
    assert by_name["refresh_query_tools"].annotations.destructiveHint is False


def test_every_static_tool_declares_an_output_schema():
    for tool in STATIC_TOOLS:
        assert tool.outputSchema is not None, tool.name
        assert tool.outputSchema["type"] == "object"


@pytest.mark.asyncio
async def test_list_databases_returns_an_object_so_it_can_be_structured_content():
    """structuredContent must be a JSON object — a bare array cannot be one."""
    client, catalog = _fixture()
    result = await dispatch_tool(client, catalog, "list_databases", {})
    assert result["databases"][0]["alias"] == "pg_test"
    await client.aclose()


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises():
    client, catalog = _fixture()
    await catalog.tools()
    with pytest.raises(ApiHubError) as exc:
        await dispatch_tool(client, catalog, "query_nope_nope", {})
    assert exc.value.status_code == 404
    await client.aclose()


def test_create_server_returns_named_server():
    client, catalog = _fixture()
    server = create_server(client, catalog)
    assert server.name == "oracle-api-hub"


def test_server_declares_tools_list_changed_capability():
    """The catalog really does change (TTL refresh, refresh_query_tools), so the
    server must not tell clients its tool list is static."""
    client, catalog = _fixture()
    server = create_server(client, catalog)
    # the HTTP session manager calls this with no arguments — the default must hold
    caps = server.create_initialization_options().capabilities
    assert caps.tools.listChanged is True


@pytest.mark.asyncio
async def test_refresh_reports_whether_the_tool_set_actually_changed():
    defs = [DEFINITION]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            return httpx.Response(200, json=defs)
        return httpx.Response(404, json={"detail": "nope"})

    client = ApiHubClient("http://hub", transport=httpx.MockTransport(handler))
    catalog = ToolCatalog(client, db_aliases=["pg_test"])

    first = await dispatch_tool(client, catalog, "refresh_query_tools", {})
    assert first["query_tools"] == 1
    assert first["changed"] is True  # nothing was cached before

    assert (await dispatch_tool(client, catalog, "refresh_query_tools", {}))["changed"] is False

    defs.append(DEFINITION | {"name": "another_report"})
    third = await dispatch_tool(client, catalog, "refresh_query_tools", {})
    assert third["query_tools"] == 2
    assert third["changed"] is True
    await client.aclose()
