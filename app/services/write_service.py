"""Service layer for writing data back to Oracle (INSERT/UPDATE/DELETE).

Supports both synchronous execution and async mode via Celery task queue.
Uses parameterized statements only — never string-interpolated SQL.
"""

import logging
import re
from typing import Any

from app.db.oracle_pool import get_connection

logger = logging.getLogger(__name__)

# Strict table name validation to prevent injection
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.#]{0,127}$")


def validate_table_name(table: str) -> str:
    if not _TABLE_RE.match(table):
        raise ValueError(f"Invalid table name: {table}")
    return table


def execute_write(
    operation: str,
    table: str,
    data: list[dict[str, Any]],
    where: dict[str, Any] | None = None,
) -> int:
    """Execute a DML operation and return the number of affected rows.

    This is called either directly (sync) or from Celery tasks (async).
    """
    table = validate_table_name(table)
    total_affected = 0

    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            if operation == "insert":
                total_affected = _do_insert(cursor, table, data)
            elif operation == "update":
                total_affected = _do_update(cursor, table, data, where)
            elif operation == "delete":
                total_affected = _do_delete(cursor, table, where)
            else:
                raise ValueError(f"Unsupported operation: {operation}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    return total_affected


def _do_insert(cursor, table: str, data: list[dict[str, Any]]) -> int:
    """Batch insert using executemany for performance."""
    if not data:
        return 0
    columns = list(data[0].keys())
    col_str = ", ".join(columns)
    bind_str = ", ".join(f":{c}" for c in columns)
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({bind_str})"
    cursor.executemany(sql, data)
    return cursor.rowcount


def _do_update(cursor, table: str, data: list[dict[str, Any]], where: dict[str, Any] | None) -> int:
    """Update rows. Each dict in data provides SET values; where provides filter."""
    if not data:
        return 0

    affected = 0
    for row in data:
        set_cols = [f"{k} = :set_{k}" for k in row]
        set_clause = ", ".join(set_cols)
        binds: dict[str, Any] = {f"set_{k}": v for k, v in row.items()}

        where_clause = "1=1"
        if where:
            where_parts = [f"{k} = :wh_{k}" for k in where]
            where_clause = " AND ".join(where_parts)
            binds.update({f"wh_{k}": v for k, v in where.items()})

        sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
        cursor.execute(sql, binds)
        affected += cursor.rowcount

    return affected


def _do_delete(cursor, table: str, where: dict[str, Any] | None) -> int:
    """Delete rows matching the WHERE clause."""
    where_clause = "1=1"
    binds: dict[str, Any] = {}
    if where:
        where_parts = [f"{k} = :wh_{k}" for k in where]
        where_clause = " AND ".join(where_parts)
        binds = {f"wh_{k}": v for k, v in where.items()}

    sql = f"DELETE FROM {table} WHERE {where_clause}"
    cursor.execute(sql, binds)
    return cursor.rowcount
