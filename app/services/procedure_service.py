"""Service layer for executing stored procedures and functions across engines."""

import logging
from typing import Any

from app.db.oracle_pool import get_connection, get_dialect_for

logger = logging.getLogger(__name__)


def execute_procedure(
    name: str,
    params: dict[str, Any],
    out_params: list[str] | None = None,
    is_function: bool = False,
    db_alias: str | None = None,
) -> dict[str, Any]:
    """Execute a stored procedure or function.

    Args:
        name: Fully qualified name (e.g. "PKG_EXAMPLE.MY_PROC").
        params: Input parameters as key-value pairs.
        out_params: List of OUT parameter names. Their types are inferred
                    or default to string (VARCHAR2).
        is_function: If True, calls as a function and captures the return value.
        db_alias: Database alias to execute against.

    Returns:
        Dictionary with 'result' (function return) and 'out_params'.
    """
    out_params = out_params or []
    dialect = get_dialect_for(db_alias)

    with get_connection(db_alias) as conn:
        cursor = conn.cursor()
        try:
            if is_function:
                result = dialect.call_function(cursor, name, params)
                return {"result": dialect.convert_value(result), "out_params": {}}
            else:
                out_values = dialect.call_procedure(cursor, name, params, out_params)
                conn.commit()
                return {"result": None, "out_params": out_values}
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
