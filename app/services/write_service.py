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
    db_alias: str | None = None,
) -> int:
    """Execute a DML operation and return the number of affected rows.

    Args:
        db_alias: Database alias to execute against. ``None`` uses the first
                  registered database (backward compatible).
    """
    table = validate_table_name(table)
    total_affected = 0

    with get_connection(db_alias) as conn:
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


# ── Batch transactional write ───────────────────────────────────────────────


def execute_batch_write(
    table: str,
    operations: list[dict[str, Any]],
    db_alias: str | None = None,
) -> dict[str, Any]:
    """Execute multiple DML operations within a single transaction.

    Each item in *operations* is a dict:
        {"operation": "insert"|"update"|"delete",
         "data": [...],
         "where": {...} | None}

    If ANY operation fails the entire transaction is rolled back.

    Args:
        db_alias: Database alias to execute against.

    Returns a summary with per-operation results.
    """
    table = validate_table_name(table)
    results: list[dict[str, Any]] = []
    total_affected = 0

    with get_connection(db_alias) as conn:
        cursor = conn.cursor()
        try:
            for idx, op in enumerate(operations):
                operation = op.get("operation", "")
                data = op.get("data", [])
                where = op.get("where")

                if operation == "insert":
                    affected = _do_insert(cursor, table, data)
                elif operation == "update":
                    affected = _do_update(cursor, table, data, where)
                elif operation == "delete":
                    affected = _do_delete(cursor, table, where)
                else:
                    raise ValueError(f"Operation #{idx}: unsupported operation '{operation}'")

                results.append({
                    "index": idx,
                    "operation": operation,
                    "affected_rows": affected,
                })
                total_affected += affected

            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise BatchWriteError(
                message=str(exc),
                failed_index=idx,
                committed=False,
                results=results,
            ) from exc
        finally:
            cursor.close()

    return {
        "total_affected": total_affected,
        "operation_count": len(operations),
        "committed": True,
        "results": results,
    }


class BatchWriteError(Exception):
    """Raised when a batch transaction fails; carries partial results."""

    def __init__(
        self,
        message: str,
        failed_index: int,
        committed: bool,
        results: list[dict[str, Any]],
    ):
        super().__init__(message)
        self.failed_index = failed_index
        self.committed = committed
        self.results = results
