"""MCP server core: dynamic query tools + static schema tools.

Every tool call is proxied to the REST API (thin proxy) — see client.py.
Handlers are plain functions so tests can call them without a transport.
"""

import logging

from mcp.server import NotificationOptions, Server
from mcp.shared.exceptions import McpError
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequest,
    ErrorData,
    ServerNotification,
    Tool,
    ToolAnnotations,
    ToolListChangedNotification,
)

from mcp_server.catalog import FETCH_MODES, ToolCatalog, read_only_annotations
from mcp_server.client import ApiHubClient, ApiHubError
from mcp_server.resources import read_resource, resource_list

logger = logging.getLogger(__name__)

STATIC_TOOLS = [
    Tool(
        name="list_databases",
        title="List databases",
        description="List the configured database connections (alias, engine type, description).",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        outputSchema={
            "type": "object",
            "properties": {
                "databases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "alias": {"type": "string", "description": "Use this as the `db` argument"},
                            "db_type": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "additionalProperties": True,
                    },
                }
            },
            "required": ["databases"],
            "additionalProperties": False,
        },
        annotations=read_only_annotations(),
    ),
    Tool(
        name="describe_table",
        title="Describe table",
        description=(
            "Return column metadata and primary-key info for a table or view. "
            "Supports schema-qualified names (e.g. HR.EMPLOYEES)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "db": {"type": "string", "description": "Database alias (see list_databases)"},
                "table": {
                    "type": "string",
                    "description": "Table or view name, optionally schema-qualified (NAME or SCHEMA.NAME)",
                },
            },
            "required": ["db", "table"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "owner": {"type": ["string", "null"]},
                "table_name": {"type": "string"},
                "column_count": {"type": "integer"},
                "primary_keys": {"type": "array", "items": {"type": "string"}},
                "columns": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["table", "columns"],
            "additionalProperties": True,
        },
        annotations=read_only_annotations(),
    ),
    Tool(
        name="refresh_query_tools",
        title="Refresh query tools",
        description=(
            "Re-read SQL definitions from the API Hub and rebuild the query tool list. "
            "Call this when a definition was just added or changed. Reports any definition "
            "dropped for a tool-name collision ('shadowed') or with a truncated name."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        outputSchema={
            "type": "object",
            "properties": {
                "query_tools": {"type": "integer", "description": "Number of query tools after the rebuild"},
                "changed": {"type": "boolean", "description": "Whether the tool list differs from before"},
                "shadowed": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Definitions dropped because their tool name collides with an earlier one",
                },
                "truncated": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Tools whose name was cut at the length cap",
                },
            },
            "required": ["query_tools", "changed"],
            "additionalProperties": False,
        },
        # Rebuilding the catalog mutates this process's state, so it is not
        # read-only; it is however safe to repeat and destroys nothing.
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    ),
]


async def list_all_tools(catalog: ToolCatalog) -> list[Tool]:
    return STATIC_TOOLS + await catalog.tools()


async def dispatch_tool(client: ApiHubClient, catalog: ToolCatalog, name: str, args: dict) -> dict:
    """Run a tool and return its result as a JSON object.

    Always an object, never a bare array: the SDK turns a returned dict into the
    result's ``structuredContent`` (plus a serialized text block), and MCP
    defines structured content as a JSON object — so ``list_databases`` wraps
    its list rather than returning it naked.
    """
    args = dict(args or {})
    if name == "list_databases":
        return {"databases": await client.list_databases()}
    if name == "describe_table":
        return await client.describe_table(args["db"], args["table"])
    if name == "refresh_query_tools":
        return await catalog.refresh()

    target = catalog.target(name)
    if target is None:
        # The map only fills as callers list tools, so a client working from a
        # tool list it cached across a server restart can arrive here first.
        # Load once before giving up.
        await catalog.tools()
        target = catalog.target(name)
    if target is None:
        raise ApiHubError(404, f"Unknown tool: {name}")
    page = int(args.pop(target.page_key, 1) or 1)
    page_size = min(int(args.pop(target.page_size_key, 50) or 50), catalog.page_size_max)
    # Enforced here as well as in the tool's enum: the schema is advice to the
    # model, this is the boundary. Anything outside FETCH_MODES — notably "all"
    # and "batch" — must never reach the REST API from a tool call.
    fetch_mode = str(args.pop("fetch_mode", "page") or "page")
    if fetch_mode not in FETCH_MODES:
        raise ApiHubError(400, f"Unsupported fetch_mode '{fetch_mode}'; allowed: {', '.join(FETCH_MODES)}")
    # Same boundary reasoning as fetch_mode: the enum is advice to the model.
    # A `view` sent to a target that has none is dropped rather than forwarded —
    # it would otherwise become a bind parameter and fail deep in the query.
    view = args.pop("view", None)
    if view is not None:
        view = str(view)
        if not target.views:
            view = None
        elif view not in target.views:
            raise ApiHubError(400, f"Unsupported view '{view}' for {name}; allowed: {', '.join(target.views)}")

    # A model that reads "array of strings" and sends "A, B" anyway is common
    # enough that splitting here is cheaper than a round trip spent correcting it.
    def _string_list(key: str) -> list[str] | None:
        raw = args.pop(key, None)
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = raw.split(",")
        if not isinstance(raw, list):
            raise ApiHubError(400, f"'{key}' must be an array of column names")
        return [str(item).strip() for item in raw if str(item).strip()] or None

    columns = _string_list(target.columns_key)
    order_by = _string_list(target.order_by_key)
    return await client.execute_query(
        target.db_alias,
        target.query_name,
        args,
        fetch_mode=fetch_mode,
        page=page,
        page_size=page_size,
        view=view,
        columns=columns,
        order_by=order_by,
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


class _ApiHubServer(Server):
    """Server that declares ``tools.listChanged`` by default.

    The tool list is not static — it is rebuilt on every ``tools/list`` and by
    ``refresh_query_tools`` — so the capability has to say so or clients will
    cache ``tools/list`` forever. The default matters because
    ``StreamableHTTPSessionManager`` calls ``create_initialization_options()``
    with no arguments; there is no other seam to pass NotificationOptions
    through. The stdio entrypoint still passes its own options explicitly,
    advertising the capability only when the watcher is actually running.
    """

    def __init__(self, name: str):
        super().__init__(name)
        self._default_notification_options = NotificationOptions(tools_changed=True)

    def create_initialization_options(self, notification_options=None, experimental_capabilities=None):
        return super().create_initialization_options(
            notification_options or self._default_notification_options,
            experimental_capabilities,
        )


def create_server(client: ApiHubClient, catalog: ToolCatalog, tracker: SessionTracker | None = None) -> Server:
    server = _ApiHubServer("oracle-api-hub")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        if tracker is not None:
            tracker.capture(server)
        return await list_all_tools(catalog)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> dict:
        if tracker is not None:
            tracker.capture(server)
        # Returning a dict lets the SDK populate both structuredContent and the
        # serialized text block, and validate the result against outputSchema.
        # Exceptions raised here are converted by the SDK into isError results.
        result = await dispatch_tool(client, catalog, name, arguments)
        if name == "refresh_query_tools" and result.get("changed"):
            await _notify_tools_changed(server)
        return result

    @server.list_resources()
    async def _list_resources():
        if tracker is not None:
            tracker.capture(server)
        return resource_list()

    @server.read_resource()
    async def _read_resource(uri):
        if tracker is not None:
            tracker.capture(server)
        # Registering these two handlers is also what declares the `resources`
        # capability — the SDK derives capabilities from the handler table
        # (Server.get_capabilities), so there is nothing else to switch on.
        return await read_resource(client, str(uri))

    _reject_unknown_tools_as_protocol_errors(server, catalog)
    return server


def _reject_unknown_tools_as_protocol_errors(server: Server, catalog: ToolCatalog) -> None:
    """Answer ``tools/call`` for an unregistered name with JSON-RPC -32602.

    The spec puts "unknown tool" in the protocol-error bucket, not the
    ``isError`` bucket that exists for failures a model can correct by retrying
    with different arguments. The SDK's ``call_tool`` decorator funnels every
    exception the handler raises into an ``isError`` result, so this check has
    to sit in front of it: ``McpError`` raised here reaches ``_handle_request``,
    which turns it into a real error response.

    The check consults the static names and the catalog's name → target map,
    and loads the catalog only when the name is not there yet (a client working
    from a list it cached across a server restart). It must NOT rebuild the
    tool list on every call: a rebuild is one REST request per alias plus two,
    every one of them charged to the caller's rate limit — which at five
    aliases made a single tool call cost eight requests and let a user burn the
    default 60/min in seven calls. The map is shared and cumulative, so a
    name another caller loaded resolves here too; that is fine — the REST
    layer still 403s an execution the caller is not permitted.

    A name the map holds for a definition that has since been deleted is
    dispatched and comes back as a 404 ``isError`` rather than -32602 — the
    bucket a model can act on, so the cheaper side to err on.

    If the catalog cannot be reached the check is skipped rather than guessed
    at — a REST outage is a tool execution error, and the inner handler already
    reports it as one.
    """
    inner = server.request_handlers[CallToolRequest]
    static_names = {tool.name for tool in STATIC_TOOLS}

    async def handler(req: CallToolRequest):
        name = req.params.name
        if name not in static_names and catalog.target(name) is None:
            try:
                await catalog.tools()
            except ApiHubError:
                return await inner(req)
            if catalog.target(name) is None:
                raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Unknown tool: {name}"))
        return await inner(req)

    server.request_handlers[CallToolRequest] = handler


async def _notify_tools_changed(server: Server) -> None:
    """Best-effort ``notifications/tools/list_changed``.

    Sent with ``related_request_id`` rather than via the session's
    ``send_tool_list_changed()`` helper: that helper leaves the id unset, which
    routes the notification to the standalone GET SSE stream. The HTTP transport
    runs stateless, so no such stream exists and the notification would be
    dropped without an error — the client would keep a stale tool list forever.
    Tying it to the originating request puts it on that request's own stream,
    which is what the transport spec allows for related messages.

    A failure here costs the client a stale tool list until its next
    ``tools/list``; it is not a reason to fail the refresh that triggered it.
    """
    try:
        ctx = server.request_context
        await ctx.session.send_notification(
            ServerNotification(ToolListChangedNotification()),
            related_request_id=ctx.request_id,
        )
    except Exception as exc:  # pragma: no cover - depends on transport state
        logger.debug("could not send tools/list_changed: %s", exc)
