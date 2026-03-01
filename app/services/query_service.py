"""Service layer for executing SQL queries against Oracle via cx_Oracle pool."""

import logging
from typing import Any

import cx_Oracle

from app.db.oracle_pool import get_connection
from app.services.sql_registry import SqlDefinition

logger = logging.getLogger(__name__)


def execute_query(
    definition: SqlDefinition,
    params: dict[str, Any],
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Execute a registered SQL definition and return (columns, rows, total).

    Wraps the query with a counting subquery and pagination using ROWNUM
    (compatible with Oracle 11g which lacks OFFSET/FETCH).
    """
    bind_params = _prepare_binds(definition, params)

    # Count query
    count_sql = f"SELECT COUNT(*) FROM ({definition.sql})"
    # Paginated query using Oracle 11g compatible ROWNUM
    offset = (page - 1) * page_size
    paginated_sql = f"""
        SELECT * FROM (
            SELECT inner_.*, ROWNUM rnum_ FROM (
                {definition.sql}
            ) inner_
            WHERE ROWNUM <= :_max_row
        )
        WHERE rnum_ > :_min_row
    """
    bind_params_page = {**bind_params, "_max_row": offset + page_size, "_min_row": offset}

    with get_connection() as conn:
        # Get total count
        cur_count = conn.cursor()
        try:
            cur_count.execute(count_sql, bind_params)
            total = cur_count.fetchone()[0]
        finally:
            cur_count.close()

        # Get page data
        cur_data = conn.cursor()
        try:
            cur_data.execute(paginated_sql, bind_params_page)
            col_names = [desc[0] for desc in cur_data.description if desc[0] != "RNUM_"]
            rows = []
            for row in cur_data:
                row_dict = {}
                for i, col in enumerate(cur_data.description):
                    if col[0] == "RNUM_":
                        continue
                    value = row[i]
                    # Convert Oracle LOB to string
                    if isinstance(value, cx_Oracle.LOB):
                        value = value.read()
                    row_dict[col[0]] = value
                rows.append(row_dict)
        finally:
            cur_data.close()

    return col_names, rows, total


def _prepare_binds(definition: SqlDefinition, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and prepare bind parameters.  Missing optional params are set to None."""
    binds: dict[str, Any] = {}
    for pname, pdef in definition.params.items():
        value = params.get(pname)
        if value is None and pdef.required:
            raise ValueError(f"Required parameter '{pname}' is missing")
        binds[pname] = value
    return binds
