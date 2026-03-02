"""Query endpoints — read tables/views as open data via registered SQL definitions.

SQL definition management (list / reload) is database-independent.
Query execution requires a ``{db}`` path parameter to target a specific database.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.databases import get_db_alias
from app.models.user import User
from app.schemas.query import QueryRequest, QueryResponse
from app.services.auth_service import PermissionChecker, get_current_user
from app.services.query_service import execute_query
from app.services.sql_registry import sql_registry

router = APIRouter(tags=["Queries"])


# ── SQL definition management (database-independent) ─────────────────────


@router.get("/queries/definitions")
def list_definitions(current_user: User = Depends(get_current_user)):
    """List all available SQL definitions (metadata only, not the SQL itself)."""
    definitions = sql_registry.list_all()
    return [
        {
            "name": d.name,
            "description": d.description,
            "permission": d.permission,
            "params": {
                k: {"type": v.type, "required": v.required, "description": v.description}
                for k, v in d.params.items()
            },
        }
        for d in definitions
    ]


@router.post("/queries/definitions/reload")
def reload_definitions(current_user: User = Depends(get_current_user)):
    """Hot-reload SQL definitions from disk (superuser only)."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser privileges required")
    sql_registry.reload()
    return {"message": "SQL definitions reloaded", "count": len(sql_registry.list_all())}


# ── Query execution (database-specific) ──────────────────────────────────


@router.post("/{db}/queries/execute", response_model=QueryResponse)
def execute(
    request: QueryRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute a registered SQL query against the specified database.

    Supports multiple fetch modes:
    - **page**: paginated (default). Use ``page`` + ``page_size``.
    - **all**: return all rows.
    - **batch**: fixed-size batch with offset. Use ``batch_size`` + ``batch_offset``.
    - **top_n**: first N rows. Use ``top_n``.
    - **one**: exactly one row (404 if none).
    """
    definition = sql_registry.get(request.query_name)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Query definition '{request.query_name}' not found")

    # Check permission
    checker = PermissionChecker(definition.permission, "read")
    checker(current_user)

    try:
        result = execute_query(
            definition,
            request.params,
            request.fetch_mode,
            page=request.page,
            page_size=request.page_size,
            batch_size=request.batch_size,
            batch_offset=request.batch_offset,
            top_n=request.top_n,
            db_alias=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query execution error: {e}")

    return QueryResponse(
        query_name=request.query_name,
        fetch_mode=request.fetch_mode,
        total=result["total"],
        page=result.get("page"),
        page_size=result.get("page_size"),
        batch_offset=result.get("batch_offset"),
        batch_size=result.get("batch_size"),
        has_more=result.get("has_more"),
        columns=result["columns"],
        data=result["data"],
    )
