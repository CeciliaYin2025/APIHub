"""Query endpoints — read tables/views as open data via registered SQL definitions."""

from fastapi import APIRouter, Depends, HTTPException

from app.models.user import User
from app.schemas.query import QueryRequest, QueryResponse
from app.services.auth_service import PermissionChecker, get_current_user
from app.services.query_service import execute_query
from app.services.sql_registry import sql_registry

router = APIRouter(prefix="/queries", tags=["Queries"])


@router.get("/definitions")
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


@router.post("/definitions/reload")
def reload_definitions(current_user: User = Depends(get_current_user)):
    """Hot-reload SQL definitions from disk (superuser only)."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser privileges required")
    sql_registry.reload()
    return {"message": "SQL definitions reloaded", "count": len(sql_registry.list_all())}


@router.post("/execute", response_model=QueryResponse)
def execute(request: QueryRequest, current_user: User = Depends(get_current_user)):
    """Execute a registered SQL query and return results in open data format."""
    definition = sql_registry.get(request.query_name)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Query definition '{request.query_name}' not found")

    # Check permission
    checker = PermissionChecker(definition.permission, "read")
    checker(current_user)

    try:
        columns, data, total = execute_query(definition, request.params, request.page, request.page_size)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query execution error: {e}")

    return QueryResponse(
        query_name=request.query_name,
        total=total,
        page=request.page,
        page_size=request.page_size,
        columns=columns,
        data=data,
    )
