"""Service layer for introspecting Oracle table/view schemas.

Queries Oracle data dictionary views (ALL_TAB_COLUMNS, ALL_CONSTRAINTS,
ALL_CONS_COLUMNS) to retrieve column metadata and primary key information.
Compatible with Oracle 11g.
"""

import logging
from typing import Any

from app.db.oracle_pool import get_connection
from app.services.write_service import validate_table_name

logger = logging.getLogger(__name__)


def describe_table(table: str, db_alias: str | None = None) -> dict[str, Any]:
    """Return column metadata and primary key info for a table or view.

    Supports both plain table names (EMPLOYEES) and schema-qualified
    names (HR.EMPLOYEES).

    Args:
        db_alias: Database alias to query against.
    """
    table = validate_table_name(table)

    # Split owner.table if qualified
    if "." in table:
        owner, tname = table.split(".", 1)
    else:
        owner = None
        tname = table

    owner_upper = owner.upper() if owner else None
    tname_upper = tname.upper()

    with get_connection(db_alias) as conn:
        columns = _get_columns(conn, owner_upper, tname_upper)
        pk_columns = _get_primary_key(conn, owner_upper, tname_upper)

    if not columns:
        raise ValueError(f"Table or view '{table}' not found or not accessible")

    # Mark PK columns
    for col in columns:
        col["is_pk"] = col["name"] in pk_columns

    return {
        "table": table,
        "owner": owner_upper,
        "table_name": tname_upper,
        "column_count": len(columns),
        "primary_keys": pk_columns,
        "columns": columns,
    }


def _get_columns(conn, owner: str | None, table_name: str) -> list[dict[str, Any]]:
    """Query ALL_TAB_COLUMNS for column metadata."""
    if owner:
        sql = """
            SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION,
                   DATA_SCALE, NULLABLE, DATA_DEFAULT, COLUMN_ID
            FROM ALL_TAB_COLUMNS
            WHERE OWNER = :owner AND TABLE_NAME = :tname
            ORDER BY COLUMN_ID
        """
        binds = {"owner": owner, "tname": table_name}
    else:
        sql = """
            SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION,
                   DATA_SCALE, NULLABLE, DATA_DEFAULT, COLUMN_ID
            FROM ALL_TAB_COLUMNS
            WHERE TABLE_NAME = :tname
            ORDER BY COLUMN_ID
        """
        binds = {"tname": table_name}

    cursor = conn.cursor()
    try:
        cursor.execute(sql, binds)
        rows = cursor.fetchall()
    finally:
        cursor.close()

    columns = []
    for row in rows:
        col_name, dtype, dlen, prec, scale, nullable, default_val, col_id = row
        # Read LOB default values
        if hasattr(default_val, "read"):
            default_val = default_val.read()
        if isinstance(default_val, str):
            default_val = default_val.strip()

        columns.append({
            "name": col_name,
            "data_type": dtype,
            "data_length": dlen,
            "data_precision": prec,
            "data_scale": scale,
            "nullable": nullable == "Y",
            "default_value": default_val,
            "column_id": col_id,
            "is_pk": False,
        })

    return columns


def _get_primary_key(conn, owner: str | None, table_name: str) -> list[str]:
    """Query ALL_CONSTRAINTS + ALL_CONS_COLUMNS for primary key columns."""
    if owner:
        sql = """
            SELECT cc.COLUMN_NAME
            FROM ALL_CONSTRAINTS c
            JOIN ALL_CONS_COLUMNS cc
              ON c.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
             AND c.OWNER = cc.OWNER
            WHERE c.OWNER = :owner
              AND c.TABLE_NAME = :tname
              AND c.CONSTRAINT_TYPE = 'P'
            ORDER BY cc.POSITION
        """
        binds = {"owner": owner, "tname": table_name}
    else:
        sql = """
            SELECT cc.COLUMN_NAME
            FROM ALL_CONSTRAINTS c
            JOIN ALL_CONS_COLUMNS cc
              ON c.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
             AND c.OWNER = cc.OWNER
            WHERE c.TABLE_NAME = :tname
              AND c.CONSTRAINT_TYPE = 'P'
            ORDER BY cc.POSITION
        """
        binds = {"tname": table_name}

    cursor = conn.cursor()
    try:
        cursor.execute(sql, binds)
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()
