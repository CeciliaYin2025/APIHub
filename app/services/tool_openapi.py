"""OpenAPI description of SQL definitions as individually callable tools.

MCP gives each definition its own tool. LLM platforms that import tools from an
OpenAPI document instead (Dify's custom tools, for one) need the same thing
expressed as one operation per definition, and ``POST /{db}/queries/execute``
cannot be that: every definition shares its path, and the bind parameters sit
nested under ``params`` — Dify builds a request body from the top-level
properties of the schema only, so it has no way to fill them in.

``POST /{db}/tools/{name}`` (``app/api/v1/tools.py``) takes a flat body, and this
module describes it. The rules deliberately mirror ``mcp_server/catalog.py``,
which this app must not import because it pulls in the MCP SDK: the same fetch
modes are withheld, page size is capped the same way, ``view`` appears only on a
definition that has one, and operation ids are the MCP tool names — so the E&O
skills, which refer to tools by those names, read the same on either platform.
"""

from typing import Any
from urllib.parse import quote

from app.services import eo_field_views
from app.services.sql_registry import ParamDef, SqlDefinition
from mcp_server.naming import tool_name

# "all" and "batch" return every matching row, which belongs in a file
# (scripts/extract_query.py), not a model's context. Same set the MCP tools offer.
TOOL_FETCH_MODES = ("page", "count", "one")

# Tool names reach the model as function names, which OpenAI-compatible APIs cap
# at 64 characters of [a-zA-Z0-9_-]; Dify expects operationIds of the same shape.
# Shorter than the MCP cap in naming.py, hence a second limit here.
OPERATION_ID_MAX_LEN = 64

PAGE_ARG = "page"
PAGE_SIZE_ARG = "page_size"

_PARAM_TYPES = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "date": {"type": "string", "format": "date"},
}
# Stated in prose as well as in `format`: models follow a format written in the
# description far more reliably than the schema keyword (see catalog.py).
_PARAM_TYPE_HINT = {"date": "Format: YYYY-MM-DD (e.g. 2026-08-01)."}

FETCH_MODE_DESCRIPTION = (
    "page: one page of rows (use page/page_size). "
    "count: the matching row count only, no rows returned — use this to size a result "
    "set instead of fetching a page and reading its 'total'. "
    "one: the first row only; errors if the query matches nothing."
)
VIEW_DESCRIPTION = (
    "Audience column set. 'mc' (default) is the full MC report; 'sales' is the "
    "narrower set sent to Sales, which drops the loss-evaluation, excess and "
    "stock-structure columns. The two use DIFFERENT NAMES for the same column — "
    "MC 'Obsolete All OH Amt' is Sales 'E&O OH (All) Amt'. A caller restricted to "
    "the sales view gets it whatever they ask for; the response says which was applied."
)


def _free_arg_name(base: str, declared: set[str]) -> str:
    name = base
    while name in declared:
        name = f"_{name}"
    return name


def pagination_arg_names(definition: SqlDefinition) -> tuple[str, str]:
    """Names for the paging knobs, stepping aside for a declared bind of the same name.

    Same rule as ``mcp_server.catalog.pagination_arg_names``: the definition's
    own parameters always keep the plain name.
    """
    declared = set(definition.params)
    return _free_arg_name(PAGE_ARG, declared), _free_arg_name(PAGE_SIZE_ARG, declared)


def operation_id(db_alias: str, definition_name: str) -> str:
    return tool_name(db_alias, definition_name)[:OPERATION_ID_MAX_LEN]


def _param_schema(pdef: ParamDef) -> dict[str, Any]:
    schema = dict(_PARAM_TYPES.get(pdef.type, {"type": "string"}))
    parts = [p for p in ((pdef.description or "").strip(), _PARAM_TYPE_HINT.get(pdef.type, "")) if p]
    if parts:
        schema["description"] = " — ".join(parts)
    return schema


def describe(db_alias: str, definition: SqlDefinition) -> str:
    """``[DW · eo] <description>`` plus ``Returns: ...`` — the MCP tool description format."""
    group = (definition.group_name or "").strip()
    header = f"[{db_alias} · {group}]" if group else f"[{db_alias}]"
    body = (definition.description or "").strip() or f"Run registered query '{definition.name}'"
    text = f"{header} {body}"
    returns = (definition.result_description or "").strip()
    if returns:
        text += f"\nReturns: {returns}"
    return text


def request_schema(definition: SqlDefinition, page_size_max: int) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, pdef in definition.params.items():
        properties[pname] = _param_schema(pdef)
        if pdef.required:
            required.append(pname)

    properties["fetch_mode"] = {
        "type": "string",
        "enum": list(TOOL_FETCH_MODES),
        "default": "page",
        "description": FETCH_MODE_DESCRIPTION,
    }
    if eo_field_views.is_governed(definition.name):
        # No default on purpose: an importer fills defaults into every call, and an
        # explicit 'mc' is not the same request as no view at all.
        properties["view"] = {"type": "string", "enum": list(eo_field_views.VIEWS), "description": VIEW_DESCRIPTION}

    page_arg, page_size_arg = pagination_arg_names(definition)
    properties[page_arg] = {"type": "integer", "minimum": 1, "default": 1, "description": "Result page number"}
    properties[page_size_arg] = {
        "type": "integer",
        "minimum": 1,
        "maximum": page_size_max,
        "default": 50,
        "description": f"Rows per page (at most {page_size_max})",
    }

    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def build_openapi(
    db_alias: str,
    definitions: list[SqlDefinition],
    *,
    server_url: str,
    page_size_max: int,
    group: str | None = None,
) -> dict[str, Any]:
    """One POST operation per definition, ordered by name.

    Two definitions whose names fold to the same operation id cannot both be
    served: the first by name wins, and the rest are listed under
    ``x-skipped-definitions`` rather than dropped silently (the MCP catalog's
    silent first-wins is exactly the trap the Admin UI's MCP Tools page exists
    to expose).
    """
    paths: dict[str, Any] = {}
    seen: set[str] = set()
    skipped: list[str] = []
    for definition in sorted(definitions, key=lambda d: d.name):
        op_id = operation_id(db_alias, definition.name)
        if op_id in seen:
            skipped.append(definition.name)
            continue
        seen.add(op_id)
        paths[f"/{quote(db_alias, safe='')}/tools/{quote(definition.name, safe='')}"] = {
            "post": {
                "operationId": op_id,
                "summary": definition.name,
                "description": describe(db_alias, definition),
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": request_schema(definition, page_size_max)}},
                },
                "responses": {
                    "200": {
                        "description": (
                            "total (rows matching before paging), columns, and data — one object per row "
                            "keyed by column name"
                        )
                    }
                },
            }
        }

    scope = f"{db_alias} · {group}" if group else db_alias
    document: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": f"API Hub tools — {scope}",
            "version": "1.0.0",
            "description": (
                "Registered SQL definitions as individually callable tools. Only the definitions the "
                "requesting credential may execute are listed."
            ),
        },
        "servers": [{"url": server_url}],
        "paths": paths,
        "components": {"securitySchemes": {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}},
        "security": [{"ApiKeyAuth": []}],
    }
    if skipped:
        document["x-skipped-definitions"] = skipped
    return document
