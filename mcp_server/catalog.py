"""Build the dynamic MCP tool catalog from registered SQL definitions.

One MCP tool per (db_alias, SQL definition). The tool's JSON schema is derived
from the definition's declared bind parameters, so the LLM sees real,
self-describing tools instead of a generic execute(name, params) black box.
"""

import logging
import time
from dataclasses import dataclass

from mcp.types import Tool

from mcp_server.client import ApiHubClient
from mcp_server.naming import (  # noqa: F401  (sanitize/tool_name re-exported: long-standing import path)
    TOOL_NAME_MAX_LEN,
    sanitize,
    tool_name,
    untruncated_tool_name,
)

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
    properties["page"] = {"type": "integer", "minimum": 1, "default": 1, "description": "Result page number"}
    properties["page_size"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": page_size_max,
        "default": 50,
        "description": "Rows per page",
    }
    return Tool(
        name=tool_name(db_alias, definition["name"]),
        description=build_description(db_alias, definition),
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    )


@dataclass
class QueryTarget:
    db_alias: str
    query_name: str


class ToolCatalog:
    """Maps MCP tool names to (db_alias, query definition) targets, with TTL refresh."""

    def __init__(
        self,
        client: ApiHubClient,
        *,
        db_aliases: list[str] | None = None,
        page_size_max: int = 100,
        ttl_seconds: int = 300,
    ):
        self._client = client
        self._db_aliases = db_aliases
        self._page_size_max = page_size_max
        self._ttl = ttl_seconds
        self._loaded_at = 0.0
        self._tools: list[Tool] = []
        self._targets: dict[str, QueryTarget] = {}
        self._shadowed: list[dict] = []
        self._truncated: list[dict] = []

    @property
    def page_size_max(self) -> int:
        return self._page_size_max

    def target(self, name: str) -> QueryTarget | None:
        return self._targets.get(name)

    def diagnostics(self) -> dict:
        """What the last load dropped or mangled.

        A shadowed definition is healthy over REST yet invisible to every LLM,
        and nothing in the MCP protocol carries that fact to the operator — so
        it rides out on ``refresh_query_tools`` and the log.
        """
        return {
            "query_tools": len(self._tools),
            "shadowed": list(self._shadowed),
            "truncated": list(self._truncated),
        }

    async def tools(self, force: bool = False) -> list[Tool]:
        stale = (time.monotonic() - self._loaded_at) > self._ttl
        if force or self._loaded_at == 0.0 or stale:
            await self._load()
        return self._tools

    async def _load(self) -> None:
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
                tools.append(tool)
                targets[tool.name] = QueryTarget(db_alias=alias, query_name=definition["name"])
        self._tools = tools
        self._targets = targets
        self._shadowed = shadowed
        self._truncated = truncated
        self._loaded_at = time.monotonic()
