"""MS SQL Server-specific dialect implementation."""

from __future__ import annotations

import re
from typing import Any

from app.db.dialect import Dialect

_TABLE_RE = re.compile(r"^[A-Za-z_@#][A-Za-z0-9_@#$.]{0,127}$")
_BIND_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


def _convert_binds(sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Convert ``:name`` bind syntax to ``%(name)s`` for pymssql."""
    converted_sql = _BIND_RE.sub(r"%(\1)s", sql)
    return converted_sql, params


class MssqlDialect(Dialect):
    """Dialect for Microsoft SQL Server using pymssql."""

    # ── Pagination ──────────────────────────────────────────────────────

    def wrap_paginated(self, sql: str, order_by: str | None = None) -> str:
        order_clause = order_by if order_by else "(SELECT NULL)"
        return f"""
            SELECT * FROM (
                SELECT sub_.*, ROW_NUMBER() OVER (ORDER BY {order_clause}) AS rnum_
                FROM ({sql}) sub_
            ) pag_
            WHERE rnum_ > :pag_offset AND rnum_ <= :pag_max_row
        """

    def pagination_binds(self, offset: int, page_size: int) -> dict[str, Any]:
        return {"pag_offset": offset, "pag_max_row": offset + page_size}

    def wrap_top_n(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT TOP (:pag_top_n) * FROM ({sql}) sub_{order}"

    def top_n_binds(self, n: int) -> dict[str, Any]:
        return {"pag_top_n": n}

    def wrap_one(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT TOP 1 * FROM ({sql}) sub_{order}"

    @property
    def pagination_column(self) -> str | None:
        return "rnum_"

    # ── Bind parameter conversion ───────────────────────────────────────

    def convert_bind_params(self, sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return _convert_binds(sql, params)

    # ── Schema introspection ────────────────────────────────────────────

    def get_columns_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
        if owner:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                       NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE,
                       COLUMN_DEFAULT, ORDINAL_POSITION
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = :owner AND TABLE_NAME = :tname
                ORDER BY ORDINAL_POSITION
            """
            return sql, {"owner": owner, "tname": table}
        else:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                       NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE,
                       COLUMN_DEFAULT, ORDINAL_POSITION
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = :tname
                ORDER BY ORDINAL_POSITION
            """
            return sql, {"tname": table}

    def get_primary_key_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
        if owner:
            sql = """
                SELECT c.name AS column_name
                FROM sys.indexes i
                JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
                JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
                JOIN sys.tables t ON i.object_id = t.object_id
                JOIN sys.schemas s ON t.schema_id = s.schema_id
                WHERE i.is_primary_key = 1
                  AND s.name = :owner
                  AND t.name = :tname
                ORDER BY ic.key_ordinal
            """
            return sql, {"owner": owner, "tname": table}
        else:
            sql = """
                SELECT c.name AS column_name
                FROM sys.indexes i
                JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
                JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
                JOIN sys.tables t ON i.object_id = t.object_id
                WHERE i.is_primary_key = 1
                  AND t.name = :tname
                ORDER BY ic.key_ordinal
            """
            return sql, {"tname": table}

    def parse_column_row(self, row: tuple) -> dict[str, Any]:
        col_name, dtype, char_len, num_prec, num_scale, nullable, default_val, ordinal = row
        if isinstance(default_val, str):
            default_val = default_val.strip()
        return {
            "name": col_name,
            "data_type": dtype,
            "data_length": char_len,
            "data_precision": num_prec,
            "data_scale": num_scale,
            "nullable": nullable == "YES",
            "default_value": default_val,
            "column_id": ordinal,
            "is_pk": False,
        }

    def normalize_table_parts(self, owner: str | None, table: str) -> tuple[str | None, str]:
        # SQL Server preserves case as-is
        return (owner, table)

    # ── Procedure / function calls ──────────────────────────────────────

    def call_procedure(self, cursor: Any, name: str, params: dict[str, Any], out_params: list[str]) -> dict[str, Any]:
        if params:
            param_parts = [f"@{k}=%({k})s" for k in params]
            sql = f"EXEC {name} {', '.join(param_parts)}"
        else:
            sql = f"EXEC {name}"
        cursor.execute(sql, params)
        # OUT params via MSSQL OUTPUT — simplified: return empty for now,
        # as pymssql doesn't natively support OUTPUT params without extra SQL
        return {}

    def call_function(self, cursor: Any, name: str, params: dict[str, Any]) -> Any:
        if params:
            placeholders = ", ".join(f"%({k})s" for k in params)
            sql = f"SELECT {name}({placeholders})"
        else:
            sql = f"SELECT {name}()"
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return row[0] if row else None

    # ── Type conversion ─────────────────────────────────────────────────

    def convert_value(self, value: Any) -> Any:
        # pymssql returns native Python types — no special conversion
        return value

    # ── DML bind placeholders ───────────────────────────────────────────

    def insert_bind_placeholder(self, col: str) -> str:
        return f"%({col})s"

    def where_bind_placeholder(self, param: str) -> str:
        return f"%({param})s"

    def table_name_regex(self) -> re.Pattern[str]:
        return _TABLE_RE

    # ── Health check ────────────────────────────────────────────────────

    def health_check_sql(self) -> str:
        return "SELECT 1"
