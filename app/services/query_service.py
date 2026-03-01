"""Service layer for executing SQL queries against Oracle via cx_Oracle pool.

Supports five fetch modes:
- page   : paginated via page + page_size (default)
- all    : return every row
- batch  : fixed-size batch with offset (for cursor-style iteration)
- top_n  : first N rows only
- one    : exactly one row
"""

import logging
from typing import Any

import cx_Oracle

from app.db.oracle_pool import get_connection
from app.services.sql_registry import SqlDefinition

logger = logging.getLogger(__name__)


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
) -> dict[str, Any]:
    """Execute a registered SQL definition and return results.

    Returns a dict with keys:
        columns, data, total, and mode-specific pagination metadata.
    """
    bind_params = _prepare_binds(definition, params)

    if fetch_mode == "page":
        return _fetch_page(definition.sql, bind_params, page, page_size)
    elif fetch_mode == "all":
        return _fetch_all(definition.sql, bind_params)
    elif fetch_mode == "batch":
        return _fetch_batch(definition.sql, bind_params, batch_size, batch_offset)
    elif fetch_mode == "top_n":
        return _fetch_top_n(definition.sql, bind_params, top_n)
    elif fetch_mode == "one":
        return _fetch_one(definition.sql, bind_params)
    else:
        raise ValueError(f"Unknown fetch_mode: {fetch_mode}")


# ── fetch modes ─────────────────────────────────────────────────────────────


def _fetch_page(
    sql: str, binds: dict[str, Any], page: int, page_size: int
) -> dict[str, Any]:
    """Paginated query using Oracle 11g compatible ROWNUM."""
    offset = (page - 1) * page_size

    count_sql = f"SELECT COUNT(*) FROM ({sql})"
    paginated_sql = _wrap_rownum(sql)
    page_binds = {**binds, "_max_row": offset + page_size, "_min_row": offset}

    with get_connection() as conn:
        total = _get_count(conn, count_sql, binds)
        columns, data = _execute_and_map(conn, paginated_sql, page_binds)

    return {
        "columns": columns,
        "data": data,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _fetch_all(sql: str, binds: dict[str, Any]) -> dict[str, Any]:
    """Return every row without pagination."""
    count_sql = f"SELECT COUNT(*) FROM ({sql})"

    with get_connection() as conn:
        total = _get_count(conn, count_sql, binds)
        columns, data = _execute_and_map(conn, sql, binds)

    return {
        "columns": columns,
        "data": data,
        "total": total,
    }


def _fetch_batch(
    sql: str, binds: dict[str, Any], batch_size: int, batch_offset: int
) -> dict[str, Any]:
    """Return a fixed-size batch starting at batch_offset.

    Useful for cursor-style iteration over large datasets. The client keeps
    incrementing ``batch_offset`` by ``batch_size`` until ``has_more`` is False.
    """
    count_sql = f"SELECT COUNT(*) FROM ({sql})"
    paginated_sql = _wrap_rownum(sql)
    page_binds = {**binds, "_max_row": batch_offset + batch_size, "_min_row": batch_offset}

    with get_connection() as conn:
        total = _get_count(conn, count_sql, binds)
        columns, data = _execute_and_map(conn, paginated_sql, page_binds)

    has_more = (batch_offset + batch_size) < total

    return {
        "columns": columns,
        "data": data,
        "total": total,
        "batch_offset": batch_offset,
        "batch_size": batch_size,
        "has_more": has_more,
    }


def _fetch_top_n(sql: str, binds: dict[str, Any], top_n: int) -> dict[str, Any]:
    """Return the first N rows only (no full count query for performance)."""
    top_sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= :_top_n"
    page_binds = {**binds, "_top_n": top_n}

    with get_connection() as conn:
        columns, data = _execute_and_map(conn, top_sql, page_binds)

    return {
        "columns": columns,
        "data": data,
        "total": len(data),
    }


def _fetch_one(sql: str, binds: dict[str, Any]) -> dict[str, Any]:
    """Return exactly one row. Raises ValueError if no rows found."""
    top_sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= 1"

    with get_connection() as conn:
        columns, data = _execute_and_map(conn, top_sql, binds)

    if not data:
        raise ValueError("Query returned no rows")

    return {
        "columns": columns,
        "data": data[:1],
        "total": 1,
    }


# ── helpers ─────────────────────────────────────────────────────────────────


def _wrap_rownum(sql: str) -> str:
    """Wrap a SQL statement with Oracle 11g ROWNUM pagination."""
    return f"""
        SELECT * FROM (
            SELECT inner_.*, ROWNUM rnum_ FROM (
                {sql}
            ) inner_
            WHERE ROWNUM <= :_max_row
        )
        WHERE rnum_ > :_min_row
    """


def _get_count(conn, count_sql: str, binds: dict[str, Any]) -> int:
    cursor = conn.cursor()
    try:
        cursor.execute(count_sql, binds)
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def _execute_and_map(
    conn, sql: str, binds: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Execute SQL and return (columns, rows_as_dicts), filtering out RNUM_."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, binds)
        col_names = [desc[0] for desc in cursor.description if desc[0] != "RNUM_"]
        rows = []
        for row in cursor:
            row_dict = {}
            for i, col in enumerate(cursor.description):
                if col[0] == "RNUM_":
                    continue
                value = row[i]
                if isinstance(value, cx_Oracle.LOB):
                    value = value.read()
                row_dict[col[0]] = value
            rows.append(row_dict)
        return col_names, rows
    finally:
        cursor.close()


def _prepare_binds(definition: SqlDefinition, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and prepare bind parameters. Missing optional params are set to None."""
    binds: dict[str, Any] = {}
    for pname, pdef in definition.params.items():
        value = params.get(pname)
        if value is None and pdef.required:
            raise ValueError(f"Required parameter '{pname}' is missing")
        binds[pname] = value
    return binds
