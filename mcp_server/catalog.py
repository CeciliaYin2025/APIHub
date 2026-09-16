"""Build the dynamic MCP tool catalog from registered SQL definitions.

One MCP tool per (db_alias, SQL definition). The tool's JSON schema is derived
from the definition's declared bind parameters, so the LLM sees real,
self-describing tools instead of a generic execute(name, params) black box.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from mcp.types import Tool, ToolAnnotations

from mcp_server.client import ApiHubClient, ApiHubError
from mcp_server.naming import (  # noqa: F401  (sanitize/tool_name re-exported: long-standing import path)
    TOOL_NAME_MAX_LEN,
    sanitize,
    tool_name,
    untruncated_tool_name,
)

# Sentinel for _load(): "no version fetched yet" is distinct from "the Hub has none" (None).
_UNFETCHED = object()

logger = logging.getLogger(__name__)

# SQL definition param types (app/services/sql_registry.py ParamDef) → JSON schema
PARAM_TYPE_MAP = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "date": {"type": "string", "format": "date"},
}

# Prose the model actually reads, appended to (never replacing) the operator's
# own description. Models follow a format stated in the description far more
# reliably than the JSON Schema ``format`` keyword, which many ignore outright —
# and the binds reach Oracle/PG/MSSQL as strings, so a wrong shape is a runtime
# error rather than something the dialect layer can rescue.
PARAM_TYPE_HINT = {
    "date": "Format: YYYY-MM-DD (e.g. 2026-08-01).",
}

# The REST fetch modes an LLM may choose. "all" and "batch" are withheld on
# purpose: both return every matching row, and a six-figure result set does not
# belong in a model's context (that is what scripts/extract_query.py is for).
# "top_n" adds nothing over "page" with a page_size.
FETCH_MODES = ("page", "count", "one")

# Mirrors app/schemas/query.py::QueryResponse. Deliberately loose — the SDK
# validates every result against this schema and fails the call on a mismatch,
# so only the fields a caller can actually rely on are required, and unlisted
# fetch-mode fields (batch_offset, has_more, ...) are allowed through.
QUERY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "query_name": {"type": "string"},
        "total": {"type": "integer", "description": "Rows matching the query before paging"},
        "page": {"type": ["integer", "null"]},
        "page_size": {"type": ["integer", "null"]},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "Column names, in order"},
        "data": {
            "type": "array",
            "items": {"type": "object"},
            "description": "One object per row, keyed by column name",
        },
    },
    "required": ["success", "total", "columns", "data"],
    "additionalProperties": True,
}


def read_only_annotations() -> ToolAnnotations:
    """Annotations for a tool that only reads.

    ``openWorldHint`` is true because the answer comes from a live external
    database, not from a closed set the client could predict.
    """
    return ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


PAGE_ARG = "page"
PAGE_SIZE_ARG = "page_size"
COLUMNS_ARG = "columns"
ORDER_BY_ARG = "order_by"

# Mirrors app/services/tool_openapi.py::ORDER_BY_DESCRIPTION.
ORDER_BY_DESCRIPTION = (
    "Sort the rows before paging, so page 1 is genuinely the top N — omit to keep the "
    "definition's own order. Write each entry as a column name, with a leading '-' for "
    "descending: [\"-OBSOLETE_OHOPO_AMT_TWD\"] is 'largest first'. Sorting happens in the "
    "database over the whole result, not over the page you fetched. "
    "Use this instead of inventing a workaround: never abuse another parameter (a far-away "
    "compare_date, say) to approximate a ranking — the other columns then come from the wrong "
    "period. An unknown column name is rejected with 400."
)

# Mirrors app/services/tool_openapi.py::COLUMNS_DESCRIPTION — the two surfaces
# describe the same REST parameter and must not drift.
COLUMNS_DESCRIPTION = (
    "Return only these columns, in this order — omit for every column. Case-insensitive; "
    "an unknown name is rejected with 400 and the available columns are listed. "
    "Worth using on wide definitions: a row carrying 89 columns costs ~2.6 KB, so a page of 100 "
    "rows is ~258 KB, while the five or six columns an answer actually needs cost a fraction of that. "
    "Pick the columns from this tool's Returns: text before calling."
)


def _free_arg_name(base: str, declared: set[str]) -> str:
    name = base
    while name in declared:
        name = f"_{name}"
    return name


def pagination_arg_names(definition: dict) -> tuple[str, str]:
    """Argument names for the injected paging knobs.

    A definition is free to declare a bind parameter of its own called ``page``
    or ``page_size``. Injecting the paging knobs under those names would both
    overwrite the declared JSON schema and make ``dispatch_tool`` swallow the
    caller's bind value, so the knobs step aside with an underscore prefix
    instead. The definition's own parameters always keep the plain name.
    """
    declared = set(definition.get("params") or {})
    return _free_arg_name(PAGE_ARG, declared), _free_arg_name(PAGE_SIZE_ARG, declared)


def columns_arg_name(definition: dict) -> str:
    """Argument name for the projection knob, stepping aside for a declared bind."""
    return _free_arg_name(COLUMNS_ARG, set(definition.get("params") or {}))


def order_by_arg_name(definition: dict) -> str:
    """Argument name for the ordering knob, stepping aside for a declared bind."""
    return _free_arg_name(ORDER_BY_ARG, set(definition.get("params") or {}))


def param_schema(pdef: dict) -> dict:
    """JSON schema for one declared bind parameter.

    The type hint and the operator's description are *joined*, not chosen
    between. The previous version assigned the description key, so a date
    parameter lost its format guidance precisely when someone had bothered to
    document it — the documented params were the ones the model got least help
    with.
    """
    ptype = pdef.get("type", "string")
    schema = dict(PARAM_TYPE_MAP.get(ptype, {"type": "string"}))
    parts = [p for p in ((pdef.get("description") or "").strip(), PARAM_TYPE_HINT.get(ptype, "")) if p]
    if parts:
        schema["description"] = " — ".join(parts)
    return schema


def build_description(db_alias: str, definition: dict) -> str:
    """The text an LLM reads to decide whether this is the right tool.

    Three parts, each optional except the header:

        [dw · HR] Headcount by department as of a frozen date.
        Returns: EMP_NO, NAME, DEPT, HIRE_DATE

    The ``group_name`` in the header is the same label the Admin UI groups the
    definition list by, so a model choosing between forty near-identical tools
    gets the operator's own taxonomy rather than having to infer one from names.
    ``result_description`` is what the query gives back — without it the only
    way to find out is to run the query, which costs a round trip and burns
    context on rows that may be the wrong shape entirely.
    """
    group = (definition.get("group_name") or "").strip()
    header = f"[{db_alias} · {group}]" if group else f"[{db_alias}]"

    body = (definition.get("description") or "").strip() or f"Run registered query '{definition['name']}'"

    text = f"{header} {body}"
    returns = (definition.get("result_description") or "").strip()
    if returns:
        text += f"\nReturns: {returns}"
    return text


def build_tool(db_alias: str, definition: dict, page_size_max: int) -> Tool:
    properties: dict = {}
    required: list[str] = []
    for pname, pdef in definition.get("params", {}).items():
        properties[pname] = param_schema(pdef)
        if pdef.get("required"):
            required.append(pname)
    properties["fetch_mode"] = {
        "type": "string",
        "enum": list(FETCH_MODES),
        "default": "page",
        "description": (
            "page: one page of rows (use page/page_size). "
            "count: the matching row count only, no rows returned — use this to size a result "
            "set instead of fetching a page and reading its 'total'. "
            "one: the first row only; errors if the query matches nothing."
        ),
    }
    # Definitions with an audience-specific column set advertise it as `views`
    # (see app/api/v1/queries.py::list_definitions). Only those get the argument:
    # offering `view` on a tool that ignores it invites the model to send it
    # everywhere.
    views = definition.get("views")
    if views:
        properties["view"] = {
            "type": "string",
            "enum": list(views),
            "default": "mc",
            "description": (
                "Audience column set. 'mc' (default) is the full MC report; 'sales' is the "
                "narrower set sent to Sales, which drops the loss-evaluation, excess and "
                "stock-structure columns. The two use DIFFERENT NAMES for the same column — "
                "MC 'Obsolete All OH Amt' is Sales 'E&O OH (All) Amt'. A caller restricted to "
                "the sales view gets it whatever they ask for; the response says which was applied."
            ),
        }

    properties[columns_arg_name(definition)] = {
        "type": "array",
        "items": {"type": "string"},
        "description": COLUMNS_DESCRIPTION,
    }
    properties[order_by_arg_name(definition)] = {
        "type": "array",
        "items": {"type": "string"},
        "description": ORDER_BY_DESCRIPTION,
    }

    page_arg, page_size_arg = pagination_arg_names(definition)
    properties[page_arg] = {"type": "integer", "minimum": 1, "default": 1, "description": "Result page number"}
    properties[page_size_arg] = {
        "type": "integer",
        "minimum": 1,
        "maximum": page_size_max,
        "default": 50,
        "description": "Rows per page",
    }
    return Tool(
        name=tool_name(db_alias, definition["name"]),
        title=f"[{db_alias}] {definition['name']}",
        description=build_description(db_alias, definition),
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        outputSchema=QUERY_OUTPUT_SCHEMA,
        annotations=read_only_annotations(),
    )


@dataclass
class QueryTarget:
    db_alias: str
    query_name: str
    # Which argument names carry paging for this tool — see pagination_arg_names().
    page_key: str = PAGE_ARG
    page_size_key: str = PAGE_SIZE_ARG
    # Which argument name carries the column projection — see columns_arg_name().
    columns_key: str = COLUMNS_ARG
    # Which argument name carries the ordering — see order_by_arg_name().
    order_by_key: str = ORDER_BY_ARG
    # Audience views this target accepts, empty when it has none. Used to reject
    # a `view` argument at the boundary rather than forwarding it blindly.
    views: tuple[str, ...] = ()


class ToolCatalog:
    """Builds the MCP tool list and maps tool names to query targets.

    Two kinds of state with deliberately different lifetimes:

    - **The tool list is held per credential, never per process.** It is
      permission-filtered per caller (``permitted_only``), and one process
      serves every caller on the HTTP transport, so a process-wide copy would
      hand whichever caller populated it to everyone else — the names,
      descriptions and parameters of tools they cannot run. The cache is keyed
      by ``cache_key_provider()`` (a fingerprint of the caller's credential; a
      constant on stdio, where there is one caller) and is only ever consulted
      for the key that populated it. It is also gated on the Hub's catalog
      fingerprint: every ``tools()`` still costs one ``catalog-version``
      request, and a hit requires the fingerprint to match *and* the entry to
      be younger than ``ttl_seconds``. The fingerprint covers aliases and
      definitions but not roles, so the TTL is what bounds how long a role
      change can go unnoticed — 60 s by default, the same lag as OAuth
      introspection. ``ttl_seconds=0`` disables the cache and every call
      reloads.
    - **The name → target map is shared and cumulative.** It is derived from
      naming alone, discloses nothing, and executing a target the caller lacks
      permission for still 403s at the REST layer. It must accumulate rather
      than be replaced: a restricted caller's load would otherwise erase the
      entries a privileged caller needs to dispatch.

    Why this matters: a full load is one ``catalog-version``, one ``/databases``
    (unless ``db_aliases`` is set) and one ``definitions`` fetch per alias —
    and every one of those counts against the caller's REST rate limit, the
    same bucket their actual queries draw from. Clients such as Open WebUI open
    a fresh MCP session and re-list tools on every chat turn, so without the
    cache the catalog, not the queries, was what exhausted the limit.
    """

    def __init__(
        self,
        client: ApiHubClient,
        *,
        db_aliases: list[str] | None = None,
        page_size_max: int = 100,
        cache_key_provider: Callable[[], str] | None = None,
        ttl_seconds: float = 0.0,
    ):
        self._client = client
        self._db_aliases = db_aliases
        self._page_size_max = page_size_max
        self._cache_key_provider = cache_key_provider or (lambda: "static")
        self._ttl = ttl_seconds
        # credential fingerprint -> (loaded_at, catalog version, tool list)
        self._cache: dict[str, tuple[float, str, list[Tool]]] = {}
        self._targets: dict[str, QueryTarget] = {}
        self._version: str | None = None
        self._last_refresh_names: set[str] | None = None

    @property
    def page_size_max(self) -> int:
        return self._page_size_max

    @property
    def version(self) -> str | None:
        """Catalog fingerprint as of the last load; None if never loaded."""
        return self._version

    def target(self, name: str) -> QueryTarget | None:
        return self._targets.get(name)

    async def tools(self, force: bool = False) -> list[Tool]:
        """The tool list for the *current* caller — this credential's cache entry
        when it is fresh, a full reload otherwise.

        ``force`` bypasses the cache (``refresh_query_tools``, the stdio watcher).
        """
        if force or self._ttl <= 0:
            tools, _ = await self._load()
            return tools

        key = self._cache_key_provider()
        version = await self._fetch_version()
        entry = self._cache.get(key)
        if entry is not None and version is not None:
            loaded_at, cached_version, tools = entry
            if cached_version == version and (time.monotonic() - loaded_at) < self._ttl:
                return tools
        tools, _ = await self._load(version=version)
        return tools

    async def refresh(self) -> dict:
        """Rebuild for the current caller and report what the load dropped.

        A shadowed definition is healthy over REST yet invisible to every LLM,
        and nothing in the MCP protocol carries that fact to the operator — so
        it rides out on ``refresh_query_tools`` and the log.

        Returned straight from this load rather than read back off the instance:
        two callers loading concurrently would otherwise be able to read each
        other's counts.

        ``changed`` compares against the previous *refresh*, not the previous
        load — plain ``tools/list`` calls (including the SDK's own outputSchema
        lookup during ``tools/call``) must not eat a change before the refresh
        that is supposed to report it. On stdio (one caller) the comparison is
        exact; on HTTP two callers with different permissions can flip it
        spuriously, which costs the client one extra ``tools/list`` — the cheap
        side to err on.
        """
        tools, diagnostics = await self._load()
        names = {t.name for t in tools}
        changed = self._last_refresh_names is None or names != self._last_refresh_names
        self._last_refresh_names = names
        return {**diagnostics, "changed": changed}

    async def _fetch_version(self) -> str | None:
        try:
            return await self._client.catalog_version()
        except ApiHubError as e:
            logger.debug("catalog-version unavailable (%s); catalog will always fully reload", e)
            return None

    async def _load(self, version: str | None | object = _UNFETCHED) -> tuple[list[Tool], dict]:
        # Read the fingerprint *before* the definitions: a change landing
        # mid-load then leaves a stale-looking version and costs one redundant
        # reload later. The other order would record a version newer than the
        # data and skip that reload entirely. tools() passes the one it already
        # fetched for the cache check so a miss does not pay for it twice.
        if version is _UNFETCHED:
            version = await self._fetch_version()

        if self._db_aliases:
            aliases = self._db_aliases
        else:
            aliases = [d["alias"] for d in await self._client.list_databases()]
        tools: list[Tool] = []
        targets: dict[str, QueryTarget] = {}
        shadowed: list[dict] = []
        truncated: list[dict] = []
        for alias in aliases:
            for definition in await self._client.list_definitions(alias):
                tool = build_tool(alias, definition, self._page_size_max)
                untruncated = untruncated_tool_name(alias, definition["name"])
                if len(untruncated) > TOOL_NAME_MAX_LEN:
                    truncated.append({"db_alias": alias, "definition": definition["name"], "tool_name": tool.name})
                    logger.warning(
                        "MCP tool name for %s/%s exceeds %d chars and was truncated to %r",
                        alias,
                        definition["name"],
                        TOOL_NAME_MAX_LEN,
                        tool.name,
                    )
                winner = targets.get(tool.name)
                if winner is not None:
                    # sanitized-name collision — first definition wins
                    shadowed.append(
                        {
                            "db_alias": alias,
                            "definition": definition["name"],
                            "tool_name": tool.name,
                            "shadowed_by": {"db_alias": winner.db_alias, "definition": winner.query_name},
                        }
                    )
                    logger.warning(
                        "MCP tool name %r for %s/%s collides with %s/%s — not exposed",
                        tool.name,
                        alias,
                        definition["name"],
                        winner.db_alias,
                        winner.query_name,
                    )
                    continue
                page_key, page_size_key = pagination_arg_names(definition)
                tools.append(tool)
                targets[tool.name] = QueryTarget(
                    db_alias=alias,
                    query_name=definition["name"],
                    page_key=page_key,
                    page_size_key=page_size_key,
                    columns_key=columns_arg_name(definition),
                    order_by_key=order_by_arg_name(definition),
                    views=tuple(definition.get("views") or ()),
                )

        # Collisions are detected against *this* load's names only — matching
        # against the accumulated map would report every tool as colliding with
        # its own entry from a previous load.
        self._targets.update(targets)
        self._version = version
        self._store(tools, version)
        return tools, {"query_tools": len(tools), "shadowed": shadowed, "truncated": truncated}

    def _store(self, tools: list[Tool], version: str | None) -> None:
        """Remember this load for the current credential.

        A load from a Hub without the version endpoint is never stored: with no
        fingerprint to compare against, the only safe cache lifetime is zero.
        """
        if self._ttl <= 0 or version is None:
            return
        now = time.monotonic()
        # Expired entries are dropped here rather than on a timer, so an idle
        # process is not left holding every credential that ever called it.
        self._cache = {k: v for k, v in self._cache.items() if (now - v[0]) < self._ttl}
        self._cache[self._cache_key_provider()] = (now, version, tools)
