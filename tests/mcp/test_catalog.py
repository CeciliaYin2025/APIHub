"""Dynamic tool catalog: definition → MCP Tool mapping, naming, per-caller isolation."""

import httpx
import pytest

from mcp_server.catalog import (
    TOOL_NAME_MAX_LEN,
    ToolCatalog,
    build_description,
    build_tool,
    param_schema,
    sanitize,
    tool_name,
)
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


def test_tool_names_may_use_the_full_128_characters_the_spec_allows():
    """The old 64 cap came from a superseded limit and was the main source of
    truncation-induced collisions; the current spec allows 1-128."""
    from mcp_server.naming import TOOL_NAME_MAX_LEN

    assert TOOL_NAME_MAX_LEN == 128
    assert len(tool_name("a" * 40, "b" * 60)) == 107  # query_ + 40 + _ + 60, untruncated
    assert len(tool_name("a" * 60, "b" * 90)) == 128  # still capped when genuinely over


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
    assert props["since"]["description"] == "Earliest order date — Format: YYYY-MM-DD (e.g. 2026-08-01)."
    assert props["page"]["type"] == "integer"
    assert props["page_size"]["maximum"] == 100
    assert tool.inputSchema["required"] == ["status"]
    assert tool.inputSchema["additionalProperties"] is False


def test_fetch_mode_offers_only_the_context_safe_modes():
    """`all` and `batch` return every matching row — never offer them to a model."""
    props = build_tool("pg_test", DEFINITION, page_size_max=100).inputSchema["properties"]
    assert props["fetch_mode"]["enum"] == ["page", "count", "one"]
    assert props["fetch_mode"]["default"] == "page"
    assert "all" not in props["fetch_mode"]["enum"]
    assert "batch" not in props["fetch_mode"]["enum"]


# ── param schema: type hint and operator description are joined, not swapped ──

DATE_HINT = "Format: YYYY-MM-DD (e.g. 2026-08-01)."


def test_date_param_keeps_both_the_hint_and_the_operator_description():
    """The regression this guards: assigning the description dropped the hint.

    A documented date param used to end up with *less* format guidance than an
    undocumented one, which is exactly backwards.
    """
    schema = param_schema({"type": "date", "description": "Earliest order date"})
    assert schema == {
        "type": "string",
        "format": "date",
        "description": f"Earliest order date — {DATE_HINT}",
    }


def test_date_param_without_a_description_still_gets_the_hint():
    assert param_schema({"type": "date"}) == {"type": "string", "format": "date", "description": DATE_HINT}
    assert param_schema({"type": "date", "description": "  "})["description"] == DATE_HINT


def test_non_date_params_carry_only_the_operator_description():
    # No hint exists for string/number, so nothing is appended and a param with
    # no description keeps no description key at all.
    assert param_schema({"type": "string", "description": "Order status code"}) == {
        "type": "string",
        "description": "Order status code",
    }
    assert param_schema({"type": "number"}) == {"type": "number"}
    assert param_schema({}) == {"type": "string"}


def test_unknown_param_type_falls_back_to_string():
    assert param_schema({"type": "timestamp", "description": "d"}) == {"type": "string", "description": "d"}


def test_param_type_map_is_not_mutated_across_calls():
    """schema is a copy — a shared dict would leak one param's description into every other."""
    param_schema({"type": "date", "description": "first"})
    assert param_schema({"type": "date"})["description"] == DATE_HINT


# ── tool description: what the LLM reads to pick the right tool ────────────


def test_description_without_group_or_returns():
    assert build_description("pg_test", DEFINITION) == "[pg_test] Open orders grouped by customer"


def test_description_includes_group_and_returns():
    defn = DEFINITION | {"group_name": "HR", "result_description": "EMP_NO, NAME, DEPT"}
    assert build_description("dw", defn) == "[dw · HR] Open orders grouped by customer\nReturns: EMP_NO, NAME, DEPT"


def test_description_falls_back_when_description_is_empty():
    # Better a generic sentence than a bare "[dw]" the model cannot act on.
    assert build_description("dw", DEFINITION | {"description": ""}) == "[dw] Run registered query 'order_summary'"
    assert build_description("dw", DEFINITION | {"description": "   "}) == "[dw] Run registered query 'order_summary'"


def test_description_ignores_blank_group_and_returns():
    """Whitespace-only values must not produce '[dw · ]' or a dangling 'Returns:'."""
    defn = DEFINITION | {"group_name": "  ", "result_description": "\n "}
    assert build_description("dw", defn) == "[dw] Open orders grouped by customer"


def test_description_tolerates_a_definition_missing_the_new_fields():
    # Definitions loaded from YAML predate both fields; the REST payload simply
    # omits them rather than sending "".
    bare = {"name": "x", "description": "d", "params": {}}
    assert build_description("dw", bare) == "[dw] d"


def test_build_tool_uses_the_description_builder():
    defn = DEFINITION | {"group_name": "Sales", "result_description": "ORDER_ID, TOTAL"}
    assert build_tool("pg_test", defn, page_size_max=100).description == build_description("pg_test", defn)


VERSION_PATH = "/api/v1/mcp/catalog-version"

# ── per-caller isolation ───────────────────────────────────────────────────


PAGING_COLLISION_DEFINITION = {
    "name": "report_by_page",
    "db_alias": "pg_test",
    "description": "Report keyed by the source system's own page label",
    "permission": "query:reports",
    "params": {
        "page": {"type": "string", "required": True, "description": "Source-system page label, e.g. P-01"},
        "page_size": {"type": "number", "required": False, "description": "Rows per printed sheet"},
    },
}


def test_declared_page_param_is_not_clobbered_by_pagination():
    tool = build_tool("pg_test", PAGING_COLLISION_DEFINITION, page_size_max=100)
    props = tool.inputSchema["properties"]
    # the definition's own binds keep their declared schema
    assert props["page"] == {"type": "string", "description": "Source-system page label, e.g. P-01"}
    assert props["page_size"] == {"type": "number", "description": "Rows per printed sheet"}
    # pagination moves aside instead of overwriting them
    assert props["_page"]["type"] == "integer"
    assert props["_page_size"]["maximum"] == 100
    assert tool.inputSchema["required"] == ["page"]


def test_pagination_keys_are_plain_when_no_collision():
    tool = build_tool("pg_test", DEFINITION, page_size_max=100)
    props = tool.inputSchema["properties"]
    assert "_page" not in props and "_page_size" not in props
    assert props["page"]["type"] == "integer"


def test_build_tool_is_annotated_read_only_and_titled():
    """Every query tool only reads. Without readOnlyHint a client has to treat
    each call as potentially destructive and prompt the user every time."""
    tool = build_tool("pg_test", DEFINITION, page_size_max=100)
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is True
    assert tool.title == "[pg_test] order_summary"


def test_build_tool_declares_the_result_envelope_as_output_schema():
    tool = build_tool("pg_test", DEFINITION, page_size_max=100)
    props = tool.outputSchema["properties"]
    assert props["total"]["type"] == "integer"
    assert props["columns"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Column names, in order",
    }
    assert props["data"]["type"] == "array"
    assert set(tool.outputSchema["required"]) == {"success", "total", "columns", "data"}


def _per_caller_client(header_provider, counter=None):
    """A Hub that filters definitions by credential, as permitted_only does."""
    everything = [
        DEFINITION,
        DEFINITION | {"name": "salary_detail", "permission": "query:salary_detail"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(request.url.path)
        if request.url.path == VERSION_PATH:
            return httpx.Response(200, json={"version": "v1"})
        if request.headers.get("X-API-Key") == "admin":
            return httpx.Response(200, json=everything)
        return httpx.Response(200, json=everything[:1])

    return ApiHubClient("http://hub", headers_provider=header_provider, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_one_callers_tool_list_never_reaches_another():
    """The HTTP transport shares one catalog across every caller.

    The tool list is permission-filtered, so caching it process-wide would serve
    whichever caller happened to populate it to everyone else — handing a
    restricted user the names, descriptions and parameters of tools they cannot
    run, which is precisely what permitted_only exists to prevent. Execution
    stays gated either way, so this is disclosure, not escalation.
    """
    creds: dict = {}
    client = _per_caller_client(lambda: creds)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])  # one instance, as create_http_app() builds it

    creds["X-API-Key"] = "admin"
    admin = {t.name for t in await catalog.tools()}

    creds["X-API-Key"] = "limited"
    limited = {t.name for t in await catalog.tools()}

    assert "query_pg_test_salary_detail" in admin
    assert "query_pg_test_salary_detail" not in limited
    await client.aclose()


@pytest.mark.asyncio
async def test_refresh_reports_the_current_callers_load_only():
    """refresh_query_tools rebuilds too, so it leaks by the same route if shared.

    Its counts come straight from its own load rather than being read back off
    the instance, so two callers refreshing concurrently cannot see each other's.
    """
    creds: dict = {}
    client = _per_caller_client(lambda: creds)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])

    creds["X-API-Key"] = "admin"
    assert (await catalog.refresh())["query_tools"] == 2

    creds["X-API-Key"] = "limited"
    assert (await catalog.refresh())["query_tools"] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_a_target_stays_resolvable_for_the_caller_who_can_see_it():
    """The name→target map is shared on purpose and must accumulate, not reset.

    It is derived from naming alone and discloses nothing, and executing a
    target without permission still 403s at the REST layer. But if a restricted
    caller's load *replaced* the map, a privileged caller's next tools/call
    would answer "Unknown tool" for a tool they legitimately hold.
    """
    creds: dict = {}
    client = _per_caller_client(lambda: creds)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])

    creds["X-API-Key"] = "admin"
    await catalog.tools()
    creds["X-API-Key"] = "limited"
    await catalog.tools()

    assert catalog.target("query_pg_test_salary_detail") is not None
    await client.aclose()


def _mock_client(definitions_by_db, databases=None, counter=None, version=None):
    """version=None leaves /mcp/catalog-version 404ing, as an older Hub would."""

    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(request.url.path)
        if request.url.path == VERSION_PATH and version is not None:
            return httpx.Response(200, json={"version": version})
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
async def test_explicit_aliases_skip_the_databases_call():
    counter: list[str] = []
    client = _mock_client({"pg_test": [DEFINITION]}, counter=counter)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    assert "/api/v1/databases" not in counter
    await client.aclose()


@pytest.mark.asyncio
async def test_every_call_refetches_for_the_current_caller():
    """No cache by design — see the ToolCatalog docstring.

    The list is permission-filtered, so holding one copy in a process that
    serves every caller is what leaked it. The cost is a handful of requests on
    the local network, once per connection.
    """
    counter: list[str] = []
    client = _mock_client({"pg_test": [DEFINITION]}, counter=counter)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    await catalog.tools()
    await catalog.tools(force=True)  # force is a no-op: every call already reloads
    assert counter.count("/api/v1/pg_test/queries/definitions") == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_an_empty_catalog_is_not_an_error():
    client = _mock_client({"pg_test": []})
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    assert await catalog.tools() == []
    assert (await catalog.refresh())["query_tools"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_target_records_the_pagination_keys_used_by_the_tool():
    client = _mock_client({"pg_test": [DEFINITION, PAGING_COLLISION_DEFINITION]})
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    assert catalog.target("query_pg_test_order_summary").page_key == "page"
    assert catalog.target("query_pg_test_report_by_page").page_key == "_page"
    assert catalog.target("query_pg_test_report_by_page").page_size_key == "_page_size"
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


# ── diagnostics: what the load dropped or mangled ──────────────────────────


@pytest.mark.asyncio
async def test_collision_is_reported_and_logged(caplog):
    defs = [DEFINITION, DEFINITION | {"name": "order summary"}]
    client = _mock_client({"pg_test": defs})
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    with caplog.at_level("WARNING", logger="mcp_server.catalog"):
        diag = await catalog.refresh()
    assert diag["query_tools"] == 1
    assert diag["truncated"] == []
    assert diag["shadowed"] == [
        {
            "db_alias": "pg_test",
            "definition": "order summary",
            "tool_name": "query_pg_test_order_summary",
            "shadowed_by": {"db_alias": "pg_test", "definition": "order_summary"},
        }
    ]
    # The drop is otherwise invisible: healthy over REST, absent from every LLM.
    assert "collides" in caplog.text
    await client.aclose()


@pytest.mark.asyncio
async def test_truncated_name_is_reported_but_still_exposed(caplog):
    long_name = "x" * 130  # untruncated name is 14 + 130 chars, past the 128 cap
    client = _mock_client({"pg_test": [DEFINITION | {"name": long_name}]})
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    with caplog.at_level("WARNING", logger="mcp_server.catalog"):
        tools = await catalog.tools()
        diag = await catalog.refresh()

    assert len(tools) == 1  # truncation is not a drop — the tool still works
    assert diag["shadowed"] == []
    assert diag["truncated"] == [{"db_alias": "pg_test", "definition": long_name, "tool_name": tools[0].name}]
    assert len(tools[0].name) == TOOL_NAME_MAX_LEN
    assert "truncated" in caplog.text
    await client.aclose()


@pytest.mark.asyncio
async def test_diagnostics_reset_on_reload():
    """A collision fixed in the Hub must disappear from diagnostics, not linger."""
    colliding = [DEFINITION, DEFINITION | {"name": "order summary"}]
    fixed = [DEFINITION]
    current = {"pg_test": colliding}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == VERSION_PATH:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=current["pg_test"])

    client = ApiHubClient("http://hub", transport=httpx.MockTransport(handler))
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    assert len((await catalog.refresh())["shadowed"]) == 1

    current["pg_test"] = fixed
    assert await catalog.refresh() == {"query_tools": 1, "changed": False, "shadowed": [], "truncated": []}
    await client.aclose()


# ── the fingerprint is still recorded, for the stdio watcher to compare ────


@pytest.mark.asyncio
async def test_a_hub_without_the_version_endpoint_still_works():
    """Degrades to the old always-refetch behaviour rather than serving a stale list."""
    counter: list[str] = []
    client = _mock_client({"pg_test": [DEFINITION]}, counter=counter, version=None)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])

    await catalog.tools()
    assert catalog.version is None
    await catalog.tools()
    assert counter.count("/api/v1/pg_test/queries/definitions") == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_version_is_read_before_the_definitions():
    """Order matters: a change landing mid-load must cost a redundant reload, not a missed one."""
    counter: list[str] = []
    client = _mock_client({"pg_test": [DEFINITION]}, counter=counter, version="v1")
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    assert counter.index(VERSION_PATH) < counter.index("/api/v1/pg_test/queries/definitions")
    await client.aclose()
