"""MCP resources (eo://data-dictionary) and the `view` argument on tools."""

import json

import httpx
import pytest
from mcp.types import ListResourcesRequest, ReadResourceRequest

from mcp_server.catalog import ToolCatalog, build_tool
from mcp_server.client import ApiHubClient, ApiHubError
from mcp_server.resources import EO_DICTIONARY_URI, is_known, read_resource, resource_list
from mcp_server.server import create_server, dispatch_tool

VIEW_DEFINITION = {
    "name": "eo_summary",
    "db_alias": "DW",
    "description": "E&O summary by site",
    "permission": "query:eo_summary",
    "params": {"frozen_date": {"type": "date", "required": True, "description": ""}},
    "views": ["mc", "sales"],
}

PLAIN_DEFINITION = {
    "name": "eo_detail",
    "db_alias": "DW",
    "description": "E&O detail",
    "permission": "query:eo_detail",
    "params": {"frozen_date": {"type": "date", "required": True, "description": ""}},
    "views": None,
}

DICTIONARY = {
    "success": True,
    "views": ["mc", "sales"],
    "default_view": "mc",
    "caller_view": "mc",
    "caller_view_forced": False,
    "governed_definitions": ["eo_summary"],
    "field_count": 1,
    "fields": [
        {
            "db_field": "OBSOLETE_OH_ALL_AMT_TWD",
            "mc_label": "Obsolete All OH Amt",
            "sales_label": "E&O OH (All) Amt",
            "group": "obsolete",
            "views": ["mc", "sales"],
            "mc_only": False,
            "note": None,
        }
    ],
}


def _fixture(seen=None, definitions=None, dictionary_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/v1/DW/queries/definitions":
            return httpx.Response(200, json=definitions or [VIEW_DEFINITION, PLAIN_DEFINITION])
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json=[{"alias": "DW", "db_type": "mssql", "description": "t"}])
        if request.url.path == "/api/v1/DW/queries/execute":
            return httpx.Response(200, json={"success": True, "total": 1, "columns": ["A"], "data": [{"A": 1}]})
        if request.url.path == "/api/v1/eo/field-dictionary":
            if dictionary_status != 200:
                return httpx.Response(dictionary_status, json={"detail": "nope"})
            return httpx.Response(200, json=DICTIONARY)
        return httpx.Response(404, json={"detail": "nope"})

    client = ApiHubClient("http://hub", transport=httpx.MockTransport(handler))
    catalog = ToolCatalog(client, db_aliases=["DW"], page_size_max=100)
    return client, catalog


# ── the resource ─────────────────────────────────────────────────────────────


def test_resource_is_listed_with_a_json_mime_type():
    resources = resource_list()
    assert len(resources) == 1
    assert str(resources[0].uri) == EO_DICTIONARY_URI
    assert resources[0].mimeType == "application/json"


def test_trailing_slash_is_the_same_resource():
    assert is_known("eo://data-dictionary/")
    assert not is_known("eo://something-else")


@pytest.mark.asyncio
async def test_read_returns_the_dictionary_plus_guidance():
    client, _ = _fixture()
    payload = json.loads(await read_resource(client, EO_DICTIONARY_URI))

    assert payload["fields"][0]["db_field"] == "OBSOLETE_OH_ALL_AMT_TWD"
    # The rename is the one thing a model cannot infer from a column list.
    assert "E&O" in payload["guidance"] and "Obsolete" in payload["guidance"]
    await client.aclose()


@pytest.mark.asyncio
async def test_reading_an_unknown_uri_is_a_404():
    client, _ = _fixture()
    with pytest.raises(ApiHubError) as exc:
        await read_resource(client, "eo://not-a-resource")
    assert exc.value.status_code == 404
    await client.aclose()


@pytest.mark.asyncio
async def test_rest_failure_surfaces_rather_than_returning_an_empty_dictionary():
    client, _ = _fixture(dictionary_status=503)
    with pytest.raises(ApiHubError):
        await read_resource(client, EO_DICTIONARY_URI)
    await client.aclose()


@pytest.mark.asyncio
async def test_dictionary_is_fetched_per_read_not_cached():
    """caller_view is per-principal and one process serves every caller."""
    seen = []
    client, _ = _fixture(seen)
    await read_resource(client, EO_DICTIONARY_URI)
    await read_resource(client, EO_DICTIONARY_URI)

    fetches = [c for c in seen if c[1].endswith("/eo/field-dictionary")]
    assert len(fetches) == 2
    await client.aclose()


# ── server wiring / capability ───────────────────────────────────────────────


def test_registering_the_handlers_declares_the_resources_capability():
    client, catalog = _fixture()
    server = create_server(client, catalog)

    assert ListResourcesRequest in server.request_handlers
    assert ReadResourceRequest in server.request_handlers

    caps = server.create_initialization_options().capabilities
    assert caps.resources is not None
    # The list is static — one URI, defined in code — so nothing to subscribe to.
    assert caps.resources.listChanged is False
    assert caps.resources.subscribe is False


def test_tools_capability_is_still_declared():
    """Adding resources must not disturb the existing tools.listChanged."""
    client, catalog = _fixture()
    caps = create_server(client, catalog).create_initialization_options().capabilities
    assert caps.tools.listChanged is True


# ── the view argument on tools ───────────────────────────────────────────────


def test_view_argument_only_appears_on_definitions_that_have_views():
    with_view = build_tool("DW", VIEW_DEFINITION, 100)
    without = build_tool("DW", PLAIN_DEFINITION, 100)

    assert with_view.inputSchema["properties"]["view"]["enum"] == ["mc", "sales"]
    assert "view" not in without.inputSchema["properties"]


def test_view_description_teaches_the_rename():
    schema = build_tool("DW", VIEW_DEFINITION, 100).inputSchema["properties"]["view"]
    assert "Obsolete All OH Amt" in schema["description"]
    assert "E&O OH (All) Amt" in schema["description"]


@pytest.mark.asyncio
async def test_view_is_forwarded_to_the_rest_api():
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()

    await dispatch_tool(client, catalog, "query_dw_eo_summary", {"frozen_date": "2026-08-01", "view": "sales"})

    body = json.loads([c for c in seen if c[1].endswith("/execute")][0][2])
    assert body["view"] == "sales"
    # And the view must not have leaked into the bind parameters.
    assert "view" not in body["params"]
    await client.aclose()


@pytest.mark.asyncio
async def test_view_is_omitted_when_not_asked_for():
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()

    await dispatch_tool(client, catalog, "query_dw_eo_summary", {"frozen_date": "2026-08-01"})

    body = json.loads([c for c in seen if c[1].endswith("/execute")][0][2])
    assert "view" not in body
    await client.aclose()


@pytest.mark.asyncio
async def test_unsupported_view_is_rejected_at_the_boundary():
    """The enum is advice to the model; this is the boundary."""
    client, catalog = _fixture()
    await catalog.tools()

    with pytest.raises(ApiHubError) as exc:
        await dispatch_tool(client, catalog, "query_dw_eo_summary", {"frozen_date": "2026-08-01", "view": "finance"})
    assert exc.value.status_code == 400
    await client.aclose()


@pytest.mark.asyncio
async def test_view_sent_to_a_tool_without_views_is_dropped_not_forwarded():
    """Otherwise it would reach the REST API as a bind parameter and fail deep."""
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()

    await dispatch_tool(client, catalog, "query_dw_eo_detail", {"frozen_date": "2026-08-01", "view": "sales"})

    body = json.loads([c for c in seen if c[1].endswith("/execute")][0][2])
    assert "view" not in body
    assert "view" not in body["params"]
    await client.aclose()
