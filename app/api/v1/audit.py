"""API usage report — statistics and per-request detail over the audit trail.

Superuser-only: these endpoints expose every caller's request parameters.
Not mounted under /{db}/ because the report compares usage *across* data
sources; db_alias is a filter, not a route segment.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.middleware.audit import record_audit
from app.models.user import User
from app.schemas.audit import AuditLogItem, AuditLogPage, FilterOptions, UsageReport
from app.services import audit_report_service as svc
from app.services.auth_service import get_current_active_superuser

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/usage", response_model=UsageReport)
def usage(
    request: Request,
    date_from: date | None = Query(None, description="Inclusive start date (default: 30 days ago)"),
    date_to: date | None = Query(None, description="Inclusive end date (default: today)"),
    principal: str | None = Query(None, description="Filter by username"),
    db_alias: str | None = Query(None, description="Filter by data source alias"),
    action: str | None = Query(None, description="Filter by API name"),
    action_type: str | None = Query(None, description="Comma-separated: query,procedure,write,definition,report"),
    group_by: str | None = Query(None, description="Comma-separated: user,db,api,day"),
    sort: str | None = Query(None, description="Sort column, '-' prefix for descending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """API usage statistics, grouped by the requested dimensions."""
    record_audit(request, "report", "usage")
    try:
        ts_from, ts_to = svc.resolve_date_range(date_from, date_to)
        groups = svc.parse_group_by(group_by)
        action_types = svc.parse_action_types(action_type)
        sort_column, sort_desc = svc.parse_sort(sort)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    clauses = svc.build_filters(ts_from, ts_to, principal=principal, db_alias=db_alias,
                                action=action, action_types=action_types)
    return svc.usage_report(pgdb, group_by=groups, clauses=clauses, sort_column=sort_column,
                            sort_desc=sort_desc, page=page, page_size=page_size)


@router.get("/filters", response_model=FilterOptions)
def filters(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """Distinct filter values seen in the window, for the UI's dropdowns."""
    record_audit(request, "report", "filters")
    try:
        ts_from, ts_to = svc.resolve_date_range(date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return svc.filter_options(pgdb, ts_from=ts_from, ts_to=ts_to)


@router.get("/logs", response_model=AuditLogPage)
def logs(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    principal: str | None = Query(None),
    db_alias: str | None = Query(None),
    action: str | None = Query(None),
    action_type: str | None = Query(None),
    status_code: int | None = Query(None),
    min_duration_ms: int | None = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """Individual requests behind the statistics, newest first."""
    record_audit(request, "report", "logs")
    try:
        ts_from, ts_to = svc.resolve_date_range(date_from, date_to)
        action_types = svc.parse_action_types(action_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    clauses = svc.build_filters(ts_from, ts_to, principal=principal, db_alias=db_alias,
                                action=action, action_types=action_types,
                                status_code=status_code, min_duration_ms=min_duration_ms)
    return svc.logs_page(pgdb, clauses=clauses, page=page, page_size=page_size,
                         inline_limit=settings.audit_params_inline_limit)


@router.get("/logs/{log_id}", response_model=AuditLogItem)
def log_detail(
    request: Request,
    log_id: int,
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """One request with its params always inlined, however large."""
    record_audit(request, "report", "log_detail")
    item = svc.get_log(pgdb, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Audit log {log_id} not found")
    return item
