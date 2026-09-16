"""The `columns` projection knob on MCP query tools.

MCP and the OpenAPI tool endpoint expose the same REST parameter, so the schema
shape and the forwarding rules are asserted on both sides. What is specific here
is the boundary: dispatch_tool must forward the caller's list without turning it
into a bind parameter, and must tolerate the comma-separated string models send
when a schema says "array".
"""

import pytest

from mcp_server.catalog import COLUMNS_ARG, QueryTarget, build_tool, columns_arg_name
from mcp_server.server import dispatch_tool

DEFINITION = {
    "name": "eo_detail",
    "group_name": "eo",
    "description": "Item-level detail.",
    "result_description": "89 columns.",
    "params": {"frozen_date": {"type": "date", "required": True}},
}


class _Client:
    def __init__(self):
        self.calls = []

    async def execute_query(self, db_alias, query_name, params, **kwargs):
        self.calls.append({"db_alias": db_alias, "query_name": query_name, "params": params, **kwargs})
        return {"columns": [], "data": [], "total": 0}


class _Catalog:
    page_size_max = 100

    def __init__(self, target):
        self._target = target

    def target(self, name):
        return self._target

    async def tools(self):
        return []


def _dispatch(args, *, definition=DEFINITION):
    client = _Client()
    target = QueryTarget(
        db_alias="DW",
        query_name=definition["name"],
        columns_key=columns_arg_name(definition),
    )
    import asyncio

    asyncio.run(dispatch_tool(client, _Catalog(target), "query_dw_eo_detail", args))
    return client.calls[0]


def test_schema_offers_columns_as_an_array_of_strings():
    schema = build_tool("DW", DEFINITION, 100).inputSchema
    prop = schema["properties"][COLUMNS_ARG]

    assert prop["type"] == "array"
    assert prop["items"] == {"type": "string"}
    assert "omit for every column" in prop["description"].lower()
    assert COLUMNS_ARG not in schema["required"], "projection is opt-in"


def test_a_definition_that_declares_its_own_columns_bind_keeps_the_plain_name():
    definition = dict(DEFINITION, params={"columns": {"type": "string"}})
    schema = build_tool("DW", definition, 100).inputSchema

    assert schema["properties"]["columns"]["type"] == "string", "the definition's own bind wins"
    assert "_columns" in schema["properties"], "the knob steps aside instead of overwriting it"
    assert columns_arg_name(definition) == "_columns"


def test_dispatch_forwards_the_list_and_keeps_it_out_of_the_binds():
    call = _dispatch({"frozen_date": "2026-09-01", "columns": ["Site", "Item_No"]})

    assert call["columns"] == ["Site", "Item_No"]
    assert call["params"] == {"frozen_date": "2026-09-01"}, "columns must not become a bind parameter"


def test_dispatch_accepts_a_comma_separated_string():
    call = _dispatch({"frozen_date": "2026-09-01", "columns": "Site, Item_No , "})

    assert call["columns"] == ["Site", "Item_No"]


def test_dispatch_sends_nothing_when_the_knob_is_absent_or_empty():
    assert _dispatch({"frozen_date": "2026-09-01"})["columns"] is None
    assert _dispatch({"frozen_date": "2026-09-01", "columns": []})["columns"] is None


def test_dispatch_rejects_a_non_list_value():
    from mcp_server.client import ApiHubError

    with pytest.raises(ApiHubError):
        _dispatch({"frozen_date": "2026-09-01", "columns": 42})
