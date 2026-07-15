"""PostgreSQL-specific dialect implementation."""

from __future__ import annotations

import re
from typing import Any

from app.db.dialect import Dialect

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_BIND_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


def _convert_binds(sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Convert ``:name`` bind syntax to ``%(name)s`` for psycopg2."""
    converted_sql = _BIND_RE.sub(r"%(\1)s", sql)
    return converted_sql, params


class PostgresqlDialect(Dialect):
    """Dialect for PostgreSQL using psycopg2."""

    # ── Pagination ──────────────────────────────────────────────────────

    def wrap_paginated(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT * FROM ({sql}) sub_{order} LIMIT :pag_limit OFFSET :pag_offset"

    def pagination_binds(self, offset: int, page_size: int) -> dict[str, Any]:
        return {"pag_limit": page_size, "pag_offset": offset}

    def wrap_top_n(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT * FROM ({sql}) sub_{order} LIMIT :pag_top_n"

    def top_n_binds(self, n: int) -> dict[str, Any]:
        return {"pag_top_n": n}

    def wrap_one(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT * FROM ({sql}) sub_{order} LIMIT 1"

    @property
    def pagination_column(self) -> str | None:
        return None  # No synthetic column added

    # ── Bind parameter conversion ───────────────────────────────────────

    def convert_bind_params(self, sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return _convert_binds(sql, params)

    # ── Schema introspection ────────────────────────────────────────────

    def get_columns_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
        if owner:
            sql = """
                SELECT column_name, data_type, character_maximum_length,
                       numeric_precision, numeric_scale, is_nullable,
                       column_default, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = :owner AND table_name = :tname
                ORDER BY ordinal_position
            """
            return sql, {"owner": owner, "tname": table}
        else:
            sql = """
                SELECT column_name, data_type, character_maximum_length,
                       numeric_precision, numeric_scale, is_nullable,
                       column_default, ordinal_position
                FROM information_schema.columns
                WHERE table_name = :tname
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY ordinal_position
            """
            return sql, {"tname": table}

    def get_primary_key_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
        if owner:
            sql = """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = :owner
                  AND tc.table_name = :tname
                  AND tc.constraint_type = 'PRIMARY KEY'
                ORDER BY kcu.ordinal_position
            """
            return sql, {"owner": owner, "tname": table}
        else:
            sql = """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_name = :tname
                  AND tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY kcu.ordinal_position
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
        # PostgreSQL uses lowercase by default
        return (owner.lower() if owner else None, table.lower())

    # ── Procedure / function calls ──────────────────────────────────────

    def call_procedure(self, cursor: Any, name: str, params: dict[str, Any], out_params: list[str]) -> dict[str, Any]:
        if params:
            placeholders = ", ".join(f"%({k})s" for k in params)
            sql = f"CALL {name}({placeholders})"
        else:
            sql = f"CALL {name}()"
        cursor.execute(sql, params)
        # PostgreSQL CALL doesn't return result sets for OUT params in the same way;
        # for procedures with OUT params, use function syntax instead
        return {}

    def call_function(self, cursor: Any, name: str, params: dict[str, Any]) -> Any:
        if params:
            placeholders = ", ".join(f"%({k})s" for k in params)
            sql = f"SELECT * FROM {name}({placeholders})"
        else:
            sql = f"SELECT * FROM {name}()"
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return row[0] if row else None

    # ── Type conversion ─────────────────────────────────────────────────

    def convert_value(self, value: Any) -> Any:
        # psycopg2 returns native Python types — no special conversion
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
