"""Per-definition tool endpoints, for LLM platforms that import tools from OpenAPI.

``POST /{db}/tools/{name}`` runs one definition from a *flat* JSON body — the
bind parameters side by side with ``fetch_mode`` / ``page`` / ``page_size`` /
``view`` — and ``GET /{db}/tools/openapi.json`` describes every definition the
caller may run in that shape. See ``app/services/tool_openapi.py`` for why
``/queries/execute`` could not serve this directly.

Execution is not reimplemented: the body is split into a ``QueryRequest`` and
handed to ``queries.execute``, so permission checks, the MC/Sales view, audit
and error mapping are the same code path as every other query. What this layer
adds is the boundary the MCP server enforces for its tools, because an LLM is on
the other end here too: no ``all``/``batch``, a page-size cap, and no argument
the definition does not declare.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import ValidationError

from app.api.v1 import queries as queries_api
from app.api.v1.databases import get_db_alias
from app.core.config import settings
from app.models.user import User
from app.schemas.query import QueryRequest, QueryResponse
from app.services import eo_field_views, tool_openapi
from app.services.auth_service import PermissionChecker, get_current_user, has_permission
from app.services.sql_registry import sql_registry

router = APIRouter(tags=["Tools"])


def _server_url(request: Request) -> str:
    # The public URL wins: behind the gateway, request.base_url is the internal
    # address, and an importer that stores it would call a host it cannot reach.
    base = settings.apihub_public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return base + settings.api_prefix


def _as_int(value: Any, name: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"'{name}' must be an integer, got {value!r}")


@router.get("/{db}/tools/openapi.json")
def tools_openapi(
    request: Request,
    group: str | None = None,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """OpenAPI 3 document with one operation per definition the caller may run.

    Authenticated and permission-filtered, like ``definitions?permitted_only=true``:
    the document is pasted into an LLM platform, so it must not advertise
    definitions the credential cannot execute. ``group`` narrows it to one
    ``group_name`` (e.g. ``eo``).
    """
    definitions = [d for d in sql_registry.list_all(db_alias=db) if has_permission(current_user, d.permission, "read")]
    if group is not None:
        definitions = [d for d in definitions if d.group_name == group]
    return tool_openapi.build_openapi(
        db,
        definitions,
        server_url=_server_url(request),
        page_size_max=settings.tool_page_size_max,
        group=group,
    )


@router.post("/{db}/tools/{query_name}", response_model=QueryResponse)
def call_tool(
    request: Request,
    query_name: str,
    arguments: dict[str, Any] | None = Body(default=None),
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Run one definition from a flat body. Response is the same as ``/queries/execute``."""
    definition = sql_registry.get(query_name, db_alias=db)
    if definition is None or not definition.enabled:
        # execute() owns the wording that tells "not found" from "disabled".
        return queries_api.execute(request, QueryRequest(query_name=query_name), db, current_user)

    # Before looking at the arguments, so an error message listing the accepted
    # parameters never reaches a caller who may not run the definition.
    PermissionChecker(definition.permission, "read")(current_user)

    args = {k: v for k, v in (arguments or {}).items() if v is not None}
    page_key, page_size_key = tool_openapi.pagination_arg_names(definition)
    page = _as_int(args.pop(page_key, None), page_key, 1)
    page_size = min(_as_int(args.pop(page_size_key, None), page_size_key, 50), settings.tool_page_size_max)

    fetch_mode = str(args.pop("fetch_mode", None) or "page")
    if fetch_mode not in tool_openapi.TOOL_FETCH_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported fetch_mode '{fetch_mode}'; allowed: {', '.join(tool_openapi.TOOL_FETCH_MODES)}",
        )

    view = args.pop("view", None)
    if view is not None and not eo_field_views.is_governed(definition.name):
        # Dropped, not forwarded: it would otherwise become a bind parameter.
        view = None

    unknown = sorted(set(args) - set(definition.params))
    if unknown:
        accepted = [*definition.params, "fetch_mode", page_key, page_size_key]
        if eo_field_views.is_governed(definition.name):
            accepted.append("view")
        raise HTTPException(
            status_code=400,
            detail=f"Unknown argument(s) for '{query_name}': {', '.join(unknown)}; accepted: {', '.join(accepted)}",
        )

    try:
        payload = QueryRequest(
            query_name=query_name,
            params=args,
            fetch_mode=fetch_mode,
            page=page,
            page_size=page_size,
            view=view,
        )
    except ValidationError as e:
        # 400 with the reason, not a 500: the model sent it and can correct it.
        raise HTTPException(status_code=400, detail="; ".join(err["msg"] for err in e.errors()))

    return queries_api.execute(request, payload, db, current_user)
