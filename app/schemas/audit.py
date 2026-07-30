"""Pydantic schemas for the API usage report endpoints."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class UsageMetrics(BaseModel):
    calls: int = 0
    success: int = 0
    failed: int = 0
    avg_duration_ms: int = 0
    max_duration_ms: int = 0
    total_rows: int = 0
    last_used_at: datetime | None = None


class UsageItem(UsageMetrics):
    """One group. Dimensions not selected via group_by come back as null."""
    principal: str | None = None
    db_alias: str | None = None
    action: str | None = None
    day: date | None = None


class UsageReport(BaseModel):
    total_groups: int
    page: int
    page_size: int
    totals: UsageMetrics
    items: list[UsageItem]


class AuditLogItem(BaseModel):
    id: int
    ts: datetime | None = None
    principal: str | None = None
    principal_type: str | None = None
    api_key_name: str | None = None
    client_ip: str | None = None
    method: str | None = None
    path: str | None = None
    db_alias: str | None = None
    action_type: str | None = None
    action: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    result_rows: int | None = None
    params: Any | None = None
    # True when params exceeded AUDIT_PARAMS_INLINE_LIMIT and must be fetched
    # from GET /audit/logs/{id} instead.
    params_omitted: bool = False
    params_bytes: int | None = None


class AuditLogPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AuditLogItem]


class FilterOptions(BaseModel):
    principals: list[str]
    db_aliases: list[str]
    actions: list[str]
