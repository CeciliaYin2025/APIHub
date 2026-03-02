"""Table schema introspection endpoints.

Requires a ``{db}`` path parameter to select the target database.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.databases import get_db_alias
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.schema_service import describe_table

router = APIRouter(tags=["Schemas"])


@router.get("/{db}/schemas/describe/{table}")
def describe(
    table: str,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Return column metadata and primary key info for a table or view.

    Supports schema-qualified names (e.g. ``HR.EMPLOYEES``).
    """
    try:
        return describe_table(table, db_alias=db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema introspection error: {e}")
