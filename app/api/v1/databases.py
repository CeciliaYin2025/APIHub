"""Database listing endpoint and shared dependency for database alias validation."""

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from app.db.multi_pool import db_registry
from app.models.user import User
from app.services.auth_service import get_current_active_superuser, get_current_user
from app.services.db_health_service import check_all_databases

router = APIRouter(prefix="/databases", tags=["Databases"])


def get_db_alias(request: Request, db: str = Path(..., description="Database alias")) -> str:
    """FastAPI dependency — validate that *db* is a registered database alias."""
    if not db_registry.has(db):
        available = ", ".join(db_registry.aliases()) or "(none)"
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db}' not configured. Available: {available}",
        )
    request.state.audit_db_alias = db
    return db


@router.get("")
def list_databases(current_user: User = Depends(get_current_user)):
    """List all configured database connections."""
    return db_registry.list_databases()


@router.get("/status")
def database_status(_admin: User = Depends(get_current_active_superuser)):
    """Check live connectivity for every configured database.

    Superuser-only: this opens real connections and returns raw driver errors.
    """
    return check_all_databases()
