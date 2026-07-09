"""Service layer for executing SQL queries via engine-agnostic pool and dialect.

Supports five fetch modes:
- page   : paginated via page + page_size (default)
- all    : return every row
- batch  : fixed-size batch with offset (for cursor-style iteration)
- top_n  : first N rows only
- one    : exactly one row
"""

import logging
import re
from typing import Any

from app.db.oracle_pool import get_connection, get_dialect_for
from app.services.sql_registry import SqlDefinition

logger = logging.getLogger(__name__)

_ORDER_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def _build_order_by(definition) -> str | None:
    """Build a safe ORDER BY clause body from a definition's order_by list.

    Re-validates each column against the identifier whitelist and direction
    against ASC/DESC (defense in depth vs YAML/DB rows that bypass Pydantic),
    then joins into "COL DIR, COL2 DIR2". Returns None for an empty list.
    Raises ValueError on any invalid column/direction.
    """
    items = getattr(definition, "order_by", None) or []
    parts: list[str] = []
    for item in items:
        column = item.get("column")
        direction_raw = item.get("direction")

        # Validate column is a string and matches the regex
        if not isinstance(column, str) or not _ORDER_COLUMN_RE.match(column):
            raise ValueError(f"Invalid order_by column: {column!r}")

        # Validate direction is a string or None, then default to "ASC"
        if direction_raw is not None and not isinstance(direction_raw, str):
            raise ValueError(f"Invalid order_by direction: {direction_raw!r}")
        direction = (direction_raw or "ASC").upper()

        if direction not in ("ASC", "DESC"):
            raise ValueError(f"Invalid order_by direction: {direction!r}")
        parts.append(f"{column} {direction}")
    return ", ".join(parts) if parts else None


# ── public API ──────────────────────────────────────────────────────────────


def execute_query(
    definition: SqlDefinition,
    params: dict[str, Any],
    fetch_mode: str = "page",
    *,
    page: int = 1,
    page_size: int = 50,
    batch_size: int = 500,
    batch_offset: int = 0,
    top_n: int = 10,
    db_alias: str | None = None,
) -> dict[str, Any]:
    """Execute a registered SQL definition and return results.

    Args:
        db_alias: Database alias to execute against. ``None`` uses the first
                  registered database (backward compatible).

    Returns a dict with keys:
        columns, data, total, and mode-specific pagination metadata.
    """
    bind_params = _prepare_binds(definition, params)
    sql = _normalize_sql(definition.sql)
    order_by = _build_order_by(definition)

    if fetch_mode == "page":
        return _fetch_page(sql, bind_params, page, page_size, db_alias, order_by)
    elif fetch_mode == "all":
        return _fetch_all(sql, bind_params, db_alias, order_by)
    elif fetch_mode == "batch":
        return _fetch_batch(sql, bind_params, batch_size, batch_offset, db_alias, order_by)
    elif fetch_mode == "top_n":
        return _fetch_top_n(sql, bind_params, top_n, db_alias, order_by)
    elif fetch_mode == "one":
        return _fetch_one(sql, bind_params, db_alias, order_by)
    else:
        raise ValueError(f"Unknown fetch_mode: {fetch_mode}")


# ── fetch modes ─────────────────────────────────────────────────────────────


def _fetch_page(
    sql: str,
    binds: dict[str, Any],
    page: int,
    page_size: int,
    db_alias: str | None = None,
    order_by: str | None = None,
) -> dict[str, Any]:
    """Paginated query using engine-specific pagination."""
    dialect = get_dialect_for(db_alias)
    offset = (page - 1) * page_size

    count_sql = dialect.wrap_count(sql)
    paginated_sql = dialect.wrap_paginated(sql, order_by)
    page_binds = {**binds, **dialect.pagination_binds(offset, page_size)}

    with get_connection(db_alias) as conn:
        total = _get_count(conn, count_sql, binds, dialect)
        columns, data = _execute_and_map(conn, paginated_sql, page_binds, dialect)

    return {
        "columns": columns,
        "data": data,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _fetch_all(
    sql: str,
    binds: dict[str, Any],
    db_alias: str | None = None,
    order_by: str | None = None,
) -> dict[str, Any]:
    """Return every row without pagination."""
    dialect = get_dialect_for(db_alias)
    count_sql = dialect.wrap_count(sql)
    data_sql = dialect.wrap_all(sql, order_by)

    with get_connection(db_alias) as conn:
        total = _get_count(conn, count_sql, binds, dialect)
        columns, data = _execute_and_map(conn, data_sql, binds, dialect)

    return {
        "columns": columns,
        "data": data,
        "total": total,
    }


def _fetch_batch(
    sql: str,
    binds: dict[str, Any],
    batch_size: int,
    batch_offset: int,
    db_alias: str | None = None,
    order_by: str | None = None,
) -> dict[str, Any]:
    """Return a fixed-size batch starting at batch_offset.

    Useful for cursor-style iteration over large datasets. The client keeps
    incrementing ``batch_offset`` by ``batch_size`` until ``has_more`` is False.
    """
    dialect = get_dialect_for(db_alias)
    count_sql = dialect.wrap_count(sql)
    paginated_sql = dialect.wrap_paginated(sql, order_by)
    page_binds = {**binds, **dialect.pagination_binds(batch_offset, batch_size)}

    with get_connection(db_alias) as conn:
        total = _get_count(conn, count_sql, binds, dialect)
        columns, data = _execute_and_map(conn, paginated_sql, page_binds, dialect)

    has_more = (batch_offset + batch_size) < total

    return {
        "columns": columns,
        "data": data,
        "total": total,
        "batch_offset": batch_offset,
        "batch_size": batch_size,
        "has_more": has_more,
    }


def _fetch_top_n(
    sql: str,
    binds: dict[str, Any],
    top_n: int,
    db_alias: str | None = None,
    order_by: str | None = None,
) -> dict[str, Any]:
    """Return the first N rows only (no full count query for performance)."""
    dialect = get_dialect_for(db_alias)
    top_sql = dialect.wrap_top_n(sql, order_by)
    page_binds = {**binds, **dialect.top_n_binds(top_n)}

    with get_connection(db_alias) as conn:
        columns, data = _execute_and_map(conn, top_sql, page_binds, dialect)

    return {
        "columns": columns,
        "data": data,
        "total": len(data),
    }


def _fetch_one(
    sql: str,
    binds: dict[str, Any],
    db_alias: str | None = None,
    order_by: str | None = None,
) -> dict[str, Any]:
    """Return exactly one row. Raises ValueError if no rows found."""
    dialect = get_dialect_for(db_alias)
    top_sql = dialect.wrap_one(sql, order_by)

    with get_connection(db_alias) as conn:
        columns, data = _execute_and_map(conn, top_sql, binds, dialect)

    if not data:
        raise ValueError("Query returned no rows")

    return {
        "columns": columns,
        "data": data[:1],
        "total": 1,
    }


# ── helpers ─────────────────────────────────────────────────────────────────


def _get_count(conn: Any, count_sql: str, binds: dict[str, Any], dialect: Any) -> int:
    converted_sql, converted_binds = dialect.convert_bind_params(count_sql, binds)
    cursor = conn.cursor()
    try:
        cursor.execute(converted_sql, converted_binds)
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def _execute_and_map(
    conn: Any, sql: str, binds: dict[str, Any], dialect: Any
) -> tuple[list[str], list[dict[str, Any]]]:
    """Execute SQL and return (columns, rows_as_dicts), filtering out pagination columns."""
    converted_sql, converted_binds = dialect.convert_bind_params(sql, binds)
    pag_col = dialect.pagination_column
    cursor = conn.cursor()
    try:
        cursor.execute(converted_sql, converted_binds)
        col_names = [desc[0] for desc in cursor.description if pag_col is None or desc[0] != pag_col]
        rows = []
        for row in cursor:
            row_dict = {}
            for i, col in enumerate(cursor.description):
                if pag_col is not None and col[0] == pag_col:
                    continue
                value = row[i]
                value = dialect.convert_value(value)
                row_dict[col[0]] = value
            rows.append(row_dict)
        return col_names, rows
    finally:
        cursor.close()


def _normalize_sql(sql: str) -> str:
    """Strip trailing whitespace and statement-terminating semicolons.

    Every fetch mode wraps the definition's SQL in a subquery
    (``... FROM (<sql>) sub_``); a trailing ``;`` would break that on every
    engine (e.g. MSSQL: "Incorrect syntax near ';'"). Only trailing terminators
    are removed — semicolons elsewhere in the statement are left intact.
    """
    normalized = sql.rstrip()
    while normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    return normalized


def _prepare_binds(definition: SqlDefinition, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and prepare bind parameters. Missing optional params are set to None."""
    binds: dict[str, Any] = {}
    for pname, pdef in definition.params.items():
        value = params.get(pname)
        if value is None and pdef.required:
            raise ValueError(f"Required parameter '{pname}' is missing")
        binds[pname] = value
    return binds
