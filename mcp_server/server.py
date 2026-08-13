"""MCP server core: dynamic query tools + static schema tools.

Every tool call is proxied to the REST API (thin proxy) — see client.py.
Handlers are plain functions so tests can call them without a transport.
"""

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from mcp_server.catalog import FETCH_MODES, ToolCatalog
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
            "Call this when a definition was just added or changed. Reports any definition "
            "dropped for a tool-name collision ('shadowed') or with a truncated name."
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
        await catalog.tools(force=True)
        return catalog.diagnostics()

    target = catalog.target(name)
    if target is None:
        raise ApiHubError(404, f"Unknown tool: {name}")
    page = int(args.pop("page", 1) or 1)
    page_size = min(int(args.pop("page_size", 50) or 50), catalog.page_size_max)
    # Enforced here as well as in the tool's enum: the schema is advice to the
    # model, this is the boundary. Anything outside FETCH_MODES — notably "all"
    # and "batch" — must never reach the REST API from a tool call.
    fetch_mode = str(args.pop("fetch_mode", "page") or "page")
    if fetch_mode not in FETCH_MODES:
        raise ApiHubError(400, f"Unsupported fetch_mode '{fetch_mode}'; allowed: {', '.join(FETCH_MODES)}")
    return await client.execute_query(
        target.db_alias,
        target.query_name,
        args,
        fetch_mode=fetch_mode,
        page=page,
        page_size=page_size,
    )


class SessionTracker:
    """Latches the live ``ServerSession`` so a background task can notify it.

    ``Server.run()`` builds the session inside its own exit stack and never
    hands it back, but every handler can reach it through the per-message
    context — so grab it there on the first call and keep it. Needed because
    ``notifications/tools/list_changed`` originates from a poller, not from a
    request.

    Stays empty under the stateless HTTP transport, where each request gets its
    own short-lived session and there is nothing to notify later. That is
    correct rather than merely tolerated: the HTTP side never declares the
    ``tools_changed`` capability.
    """

    def __init__(self) -> None:
        self.session = None

    def capture(self, server: Server) -> None:
        try:
            self.session = server.request_context.session
        except LookupError:
            # No request in flight (direct handler call in tests) — nothing to latch.
            pass


def create_server(client: ApiHubClient, catalog: ToolCatalog, tracker: SessionTracker | None = None) -> Server:
    server = Server("oracle-api-hub")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        if tracker is not None:
            tracker.capture(server)
        return await list_all_tools(catalog)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        if tracker is not None:
            tracker.capture(server)
        # Exceptions raised here are converted by the SDK into isError results.
        result = await dispatch_tool(client, catalog, name, arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

    return server
