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


# What a caller needs to route a request: pick an alias, know its engine, see
# whether the pool is up. Everything else the registry knows (dsn, host, port,
# database, user, pool sizing) describes how the Hub connects, which is the
# operator's business.
_ROUTING_FIELDS = ("alias", "db_type", "description", "pool_active")


@router.get("")
def list_databases(current_user: User = Depends(get_current_user)):
    """List configured database connections.

    Connection details are superuser-only. This endpoint requires nothing more
    than a valid login, so without the split every API key — including a
    permission-less service account, and every MCP caller through
    ``list_databases`` — could read each data source's host, service name and
    database username, production included.
    """
    rows = db_registry.list_databases()
    if current_user.is_superuser:
        return rows
    # Build new dicts: the registry returns its own objects, and redacting in
    # place would strip the fields for every later caller including superusers.
    return [{k: row[k] for k in _ROUTING_FIELDS if k in row} for row in rows]


@router.get("/status")
def database_status(_admin: User = Depends(get_current_active_superuser)):
    """Check live connectivity for every configured database.

    Superuser-only: this opens real connections and returns raw driver errors.
    """
    return check_all_databases()
