"""Read-time aggregation over api_hub_audit_logs for the API usage report.

Statistics are computed with a live GROUP BY rather than a pre-aggregated
table: the report lets the caller choose which dimensions to group by, and
retention keeps the table's size bounded. The composite indexes on
(principal|db_alias|action|action_type, ts) are what make this cheap.
"""

import json
from datetime import date, datetime, time, timedelta

from sqlalchemy import Integer, and_, asc, desc, func, select

from app.models.audit_log import AuditLog

# group_by token -> the expression to group on.
GROUP_COLUMNS = {
    "user": AuditLog.principal,
    "db": AuditLog.db_alias,
    "api": AuditLog.action,
    "day": func.date(AuditLog.ts),
}

# group_by token -> the response key it fills in.
GROUP_KEYS = {"user": "principal", "db": "db_alias", "api": "action", "day": "day"}

DEFAULT_GROUP_BY = ("user", "db", "api")

SORT_COLUMNS = ("calls", "success", "failed", "avg_duration_ms", "max_duration_ms",
                "total_rows", "last_used_at")

# The report is about data access, so admin operations (definition) and the
# report's own reads (report) are excluded unless asked for explicitly.
DEFAULT_ACTION_TYPES = ("query", "procedure", "write")
VALID_ACTION_TYPES = ("query", "procedure", "write", "definition", "report")

_METRIC_KEYS = ("calls", "success", "failed", "avg_duration_ms", "max_duration_ms",
                "total_rows", "last_used_at")

_DEFAULT_RANGE_DAYS = 30


# ── parsing / validation (pure) ──────────────────────────────────────────


def resolve_date_range(date_from: date | None, date_to: date | None) -> tuple[datetime, datetime]:
    """Turn an inclusive date range into inclusive datetime bounds.

    Defaults to the last 30 days ending today. ts is a naive DateTime on the
    platform store, so no timezone conversion happens anywhere here.
    """
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=_DEFAULT_RANGE_DAYS - 1))
    if start > end:
        raise ValueError("date_from must not be after date_to")
    return datetime.combine(start, time.min), datetime.combine(end, time.max.replace(microsecond=0))


def _parse_csv(raw: str, valid, label: str) -> list[str]:
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    unknown = [t for t in tokens if t not in valid]
    if unknown:
        raise ValueError(f"Unknown {label} value(s): {', '.join(unknown)}. Valid: {', '.join(valid)}")
    deduped = []
    for token in tokens:
        if token not in deduped:
            deduped.append(token)
    return deduped


def parse_group_by(raw: str | None) -> list[str]:
    """None -> the default dimensions; "" -> no grouping (totals only)."""
    if raw is None:
        return list(DEFAULT_GROUP_BY)
    return _parse_csv(raw, GROUP_COLUMNS, "group_by")


def parse_action_types(raw: str | None) -> list[str]:
    """None -> the data-access default; "" -> no action_type filter at all."""
    if raw is None:
        return list(DEFAULT_ACTION_TYPES)
    return _parse_csv(raw, VALID_ACTION_TYPES, "action_type")


def parse_sort(raw: str | None) -> tuple[str, bool]:
    """Return (column, descending). Column is whitelisted, so it is safe to
    hand to ORDER BY as a label reference."""
    value = (raw or "-calls").strip()
    descending = value.startswith("-")
    column = value.lstrip("-")
    if column not in SORT_COLUMNS:
        raise ValueError(f"Unknown sort column '{column}'. Valid: {', '.join(SORT_COLUMNS)}")
    return column, descending


def build_filters(ts_from: datetime, ts_to: datetime, *, principal: str | None = None,
                  db_alias: str | None = None, action: str | None = None,
                  action_types=(), status_code: int | None = None,
                  min_duration_ms: int | None = None) -> list:
    """WHERE clauses shared by every report query."""
    clauses = [AuditLog.ts >= ts_from, AuditLog.ts <= ts_to]
    if principal:
        clauses.append(AuditLog.principal == principal)
    if db_alias:
        clauses.append(AuditLog.db_alias == db_alias)
    if action:
        clauses.append(AuditLog.action == action)
    if action_types:
        clauses.append(AuditLog.action_type.in_(list(action_types)))
    if status_code is not None:
        clauses.append(AuditLog.status_code == status_code)
    if min_duration_ms is not None:
        clauses.append(AuditLog.duration_ms >= min_duration_ms)
    return clauses


# ── statement building ───────────────────────────────────────────────────


def _metric_columns() -> list:
    return [
        func.count().label("calls"),
        func.count().filter(AuditLog.status_code < 400).label("success"),
        func.count().filter(AuditLog.status_code >= 400).label("failed"),
        func.cast(func.round(func.coalesce(func.avg(AuditLog.duration_ms), 0)), Integer).label("avg_duration_ms"),
        func.coalesce(func.max(AuditLog.duration_ms), 0).label("max_duration_ms"),
        func.coalesce(func.sum(AuditLog.result_rows), 0).label("total_rows"),
        func.max(AuditLog.ts).label("last_used_at"),
    ]


def _group_columns(group_by: list[str]) -> list:
    return [GROUP_COLUMNS[token].label(GROUP_KEYS[token]) for token in group_by]


def build_usage_statement(*, group_by: list[str], clauses: list, sort_column: str,
                          sort_desc: bool, page: int, page_size: int):
    """The paginated per-group statistics query."""
    group_cols = _group_columns(group_by)
    order = desc(sort_column) if sort_desc else asc(sort_column)
    return (
        select(*group_cols, *_metric_columns())
        .where(and_(*clauses))
        .group_by(*group_cols)
        .order_by(order)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )


# ── execution ────────────────────────────────────────────────────────────


def _row_to_metrics(row) -> dict:
    return {key: row[key] for key in _METRIC_KEYS}


def usage_report(db, *, group_by: list[str], clauses: list, sort_column: str,
                 sort_desc: bool, page: int, page_size: int) -> dict:
    """Statistics for the report table, plus the grand totals row."""
    totals_row = db.execute(select(*_metric_columns()).where(and_(*clauses))).mappings().one()
    totals = _row_to_metrics(totals_row)

    if not group_by:
        return {"total_groups": 0, "page": page, "page_size": page_size, "totals": totals, "items": []}

    group_cols = _group_columns(group_by)
    total_groups = db.execute(
        select(func.count()).select_from(
            select(*group_cols).where(and_(*clauses)).group_by(*group_cols).subquery()
        )
    ).scalar_one()

    stmt = build_usage_statement(group_by=group_by, clauses=clauses, sort_column=sort_column,
                                 sort_desc=sort_desc, page=page, page_size=page_size)
    selected_keys = [GROUP_KEYS[token] for token in group_by]
    items = []
    for row in db.execute(stmt).mappings():
        item = {key: None for key in GROUP_KEYS.values()}
        item.update({key: row[key] for key in selected_keys})
        item.update(_row_to_metrics(row))
        items.append(item)

    return {"total_groups": total_groups, "page": page, "page_size": page_size,
            "totals": totals, "items": items}


def _log_to_dict(row: AuditLog, inline_limit: int | None) -> dict:
    """One detail row. Oversized params are replaced by their size so the list
    response stays small; the UI fetches them per row from get_log()."""
    params = row.params
    omitted = False
    size = None
    if params is not None:
        size = len(json.dumps(params, ensure_ascii=False, default=str).encode("utf-8"))
        if inline_limit is not None and size > inline_limit:
            params = None
            omitted = True
    return {
        "id": row.id, "ts": row.ts, "principal": row.principal,
        "principal_type": row.principal_type, "api_key_name": row.api_key_name,
        "client_ip": row.client_ip, "method": row.method, "path": row.path,
        "db_alias": row.db_alias, "action_type": row.action_type, "action": row.action,
        "status_code": row.status_code, "duration_ms": row.duration_ms,
        "result_rows": row.result_rows,
        "params": params, "params_omitted": omitted, "params_bytes": size,
    }


def logs_page(db, *, clauses: list, page: int, page_size: int, inline_limit: int | None) -> dict:
    """Newest-first page of individual requests."""
    total = db.execute(select(func.count()).select_from(AuditLog).where(and_(*clauses))).scalar_one()
    stmt = (
        select(AuditLog)
        .where(and_(*clauses))
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = [_log_to_dict(row, inline_limit) for row in db.execute(stmt).scalars()]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def get_log(db, log_id: int) -> dict | None:
    """One request with its params always inlined, however large."""
    row = db.execute(select(AuditLog).where(AuditLog.id == log_id)).scalars().first()
    return None if row is None else _log_to_dict(row, None)


def filter_options(db, *, ts_from: datetime, ts_to: datetime) -> dict:
    """Distinct values seen in the window, for the UI's filter dropdowns."""

    def distinct(column) -> list[str]:
        stmt = (
            select(column)
            .where(and_(column.isnot(None), AuditLog.ts >= ts_from, AuditLog.ts <= ts_to))
            .distinct()
            .order_by(column)
        )
        return [value for (value,) in db.execute(stmt)]

    return {
        "principals": distinct(AuditLog.principal),
        "db_aliases": distinct(AuditLog.db_alias),
        "actions": distinct(AuditLog.action),
    }
