"""Service layer for introspecting table/view schemas across database engines.

Uses the dialect system to generate engine-appropriate introspection queries
(Oracle ALL_TAB_COLUMNS, PG/MSSQL information_schema, etc.).
"""

import logging
from typing import Any

from app.db.oracle_pool import get_connection, get_dialect_for
from app.services.write_service import validate_table_name

logger = logging.getLogger(__name__)


def describe_table(table: str, db_alias: str | None = None) -> dict[str, Any]:
    """Return column metadata and primary key info for a table or view.

    Supports both plain table names (EMPLOYEES) and schema-qualified
    names (HR.EMPLOYEES).

    Args:
        db_alias: Database alias to query against.
    """
    table = validate_table_name(table, db_alias)
    dialect = get_dialect_for(db_alias)

    # Split owner.table if qualified
    if "." in table:
        owner, tname = table.split(".", 1)
    else:
        owner = None
        tname = table

    owner_norm, tname_norm = dialect.normalize_table_parts(owner, tname)

    with get_connection(db_alias) as conn:
        columns = _get_columns(conn, owner_norm, tname_norm, dialect)
        pk_columns = _get_primary_key(conn, owner_norm, tname_norm, dialect)

    if not columns:
        raise ValueError(f"Table or view '{table}' not found or not accessible")

    # Mark PK columns
    for col in columns:
        col["is_pk"] = col["name"] in pk_columns

    return {
        "table": table,
        "owner": owner_norm,
        "table_name": tname_norm,
        "column_count": len(columns),
        "primary_keys": pk_columns,
        "columns": columns,
    }


def _get_columns(conn: Any, owner: str | None, table_name: str, dialect: Any) -> list[dict[str, Any]]:
    """Query column metadata using dialect-specific SQL."""
    sql, binds = dialect.get_columns_sql(owner, table_name)
    sql, binds = dialect.convert_bind_params(sql, binds)

    cursor = conn.cursor()
    try:
        cursor.execute(sql, binds)
        rows = cursor.fetchall()
    finally:
        cursor.close()

    return [dialect.parse_column_row(row) for row in rows]


def _get_primary_key(conn: Any, owner: str | None, table_name: str, dialect: Any) -> list[str]:
    """Query primary key columns using dialect-specific SQL."""
    sql, binds = dialect.get_primary_key_sql(owner, table_name)
    sql, binds = dialect.convert_bind_params(sql, binds)

    cursor = conn.cursor()
    try:
        cursor.execute(sql, binds)
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()
