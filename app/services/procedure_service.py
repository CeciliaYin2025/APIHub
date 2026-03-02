"""Service layer for executing Oracle stored procedures and functions."""

import logging
from typing import Any

import cx_Oracle

from app.db.oracle_pool import get_connection

logger = logging.getLogger(__name__)

# Map simple type names to cx_Oracle types for OUT parameters
_TYPE_MAP = {
    "string": cx_Oracle.DB_TYPE_VARCHAR,
    "number": cx_Oracle.DB_TYPE_NUMBER,
    "date": cx_Oracle.DB_TYPE_DATE,
    "clob": cx_Oracle.DB_TYPE_CLOB,
    "cursor": cx_Oracle.CURSOR,
}


def execute_procedure(
    name: str,
    params: dict[str, Any],
    out_params: list[str] | None = None,
    is_function: bool = False,
    db_alias: str | None = None,
) -> dict[str, Any]:
    """Execute an Oracle procedure or function.

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

    with get_connection(db_alias) as conn:
        cursor = conn.cursor()
        try:
            if is_function:
                result = _call_function(cursor, name, params)
                return {"result": _convert(result), "out_params": {}}
            else:
                out_values = _call_procedure(cursor, name, params, out_params)
                conn.commit()
                return {"result": None, "out_params": out_values}
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def _call_function(cursor: cx_Oracle.Cursor, name: str, params: dict[str, Any]) -> Any:
    """Call an Oracle function and return its result."""
    # Build ordered positional args from the params dict
    param_keys = sorted(params.keys())
    args = [params[k] for k in param_keys]
    return cursor.callfunc(name, cx_Oracle.DB_TYPE_VARCHAR, args)


def _call_procedure(
    cursor: cx_Oracle.Cursor,
    name: str,
    params: dict[str, Any],
    out_param_names: list[str],
) -> dict[str, Any]:
    """Call an Oracle procedure with IN and OUT parameters."""
    # Prepare keyword arguments: input params + OUT variable placeholders
    kw_args: dict[str, Any] = dict(params)
    out_vars: dict[str, cx_Oracle.Var] = {}

    for op_name in out_param_names:
        var = cursor.var(cx_Oracle.DB_TYPE_VARCHAR, 4000)
        kw_args[op_name] = var
        out_vars[op_name] = var

    cursor.callproc(name, keywordParameters=kw_args)

    return {k: _convert(v.getvalue()) for k, v in out_vars.items()}


def _convert(value: Any) -> Any:
    """Convert Oracle-specific types to JSON-serializable Python types."""
    if isinstance(value, cx_Oracle.LOB):
        return value.read()
    return value
