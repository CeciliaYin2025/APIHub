"""MCP server core: dynamic query tools + static schema tools.

Every tool call is proxied to the REST API (thin proxy) — see client.py.
Handlers are plain functions so tests can call them without a transport.
"""

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient, ApiHubError

STATIC_TOOLS = [
    Tool(
        name="list_databases",
        description="List the configured database connections (alias, engine type, description).",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="describe_table",
        description=(
            "Return column metadata and primary-key info for a table or view. "
            "Supports schema-qualified names (e.g. HR.EMPLOYEES)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "db": {"type": "string", "description": "Database alias (see list_databases)"},
                "table": {"type": "string", "description": "Table or view name"},
            },
            "required": ["db", "table"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="refresh_query_tools",
        description=(
            "Re-read SQL definitions from the API Hub and rebuild the query tool list. "
            "Call this when a definition was just added or changed."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
]


async def list_all_tools(catalog: ToolCatalog) -> list[Tool]:
    return STATIC_TOOLS + await catalog.tools()


async def dispatch_tool(client: ApiHubClient, catalog: ToolCatalog, name: str, args: dict) -> dict | list:
    args = dict(args or {})
    if name == "list_databases":
        return await client.list_databases()
    if name == "describe_table":
        return await client.describe_table(args["db"], args["table"])
    if name == "refresh_query_tools":
        tools = await catalog.tools(force=True)
        return {"query_tools": len(tools)}

    target = catalog.target(name)
    if target is None:
        raise ApiHubError(404, f"Unknown tool: {name}")
    page = int(args.pop("page", 1) or 1)
    page_size = min(int(args.pop("page_size", 50) or 50), catalog.page_size_max)
    return await client.execute_query(target.db_alias, target.query_name, args, page=page, page_size=page_size)


def create_server(client: ApiHubClient, catalog: ToolCatalog) -> Server:
    server = Server("oracle-api-hub")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return await list_all_tools(catalog)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        # Exceptions raised here are converted by the SDK into isError results.
        result = await dispatch_tool(client, catalog, name, arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

    return server
