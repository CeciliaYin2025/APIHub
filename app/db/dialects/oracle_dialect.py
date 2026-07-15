"""Oracle-specific dialect implementation."""

from __future__ import annotations

import re
from typing import Any

from app.db.dialect import Dialect

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.#]{0,127}$")


class OracleDialect(Dialect):
    """Dialect for Oracle 11g+ using oracledb."""

    # ── Pagination ──────────────────────────────────────────────────────

    def wrap_paginated(self, sql: str, order_by: str | None = None) -> str:
        inner = f"SELECT * FROM ({sql}) ORDER BY {order_by}" if order_by else sql
        return f"""
            SELECT * FROM (
                SELECT inner_.*, ROWNUM rnum_ FROM (
                    {inner}
                ) inner_
                WHERE ROWNUM <= :pag_max_row
            )
            WHERE rnum_ > :pag_min_row
        """

    def pagination_binds(self, offset: int, page_size: int) -> dict[str, Any]:
        return {"pag_max_row": offset + page_size, "pag_min_row": offset}

    def wrap_top_n(self, sql: str, order_by: str | None = None) -> str:
        inner = f"SELECT * FROM ({sql}) ORDER BY {order_by}" if order_by else sql
        return f"SELECT * FROM ({inner}) WHERE ROWNUM <= :pag_top_n"

    def top_n_binds(self, n: int) -> dict[str, Any]:
        return {"pag_top_n": n}

    def wrap_one(self, sql: str, order_by: str | None = None) -> str:
        inner = f"SELECT * FROM ({sql}) ORDER BY {order_by}" if order_by else sql
        return f"SELECT * FROM ({inner}) WHERE ROWNUM <= 1"

    @property
    def pagination_column(self) -> str | None:
        return "RNUM_"

    # ── Bind parameter conversion ───────────────────────────────────────

    def convert_bind_params(self, sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        # Oracle natively uses :name syntax — no conversion needed
        return sql, params

    # ── Schema introspection ────────────────────────────────────────────

    def get_columns_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
        if owner:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION,
                       DATA_SCALE, NULLABLE, DATA_DEFAULT, COLUMN_ID
                FROM ALL_TAB_COLUMNS
                WHERE OWNER = :owner AND TABLE_NAME = :tname
                ORDER BY COLUMN_ID
            """
            return sql, {"owner": owner, "tname": table}
        else:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION,
                       DATA_SCALE, NULLABLE, DATA_DEFAULT, COLUMN_ID
                FROM ALL_TAB_COLUMNS
                WHERE TABLE_NAME = :tname
                ORDER BY COLUMN_ID
            """
            return sql, {"tname": table}

    def get_primary_key_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
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
            return sql, {"owner": owner, "tname": table}
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
            return sql, {"tname": table}

    def parse_column_row(self, row: tuple) -> dict[str, Any]:
        col_name, dtype, dlen, prec, scale, nullable, default_val, col_id = row
        if hasattr(default_val, "read"):
            default_val = default_val.read()
        if isinstance(default_val, str):
            default_val = default_val.strip()
        return {
            "name": col_name,
            "data_type": dtype,
            "data_length": dlen,
            "data_precision": prec,
            "data_scale": scale,
            "nullable": nullable == "Y",
            "default_value": default_val,
            "column_id": col_id,
            "is_pk": False,
        }

    def normalize_table_parts(self, owner: str | None, table: str) -> tuple[str | None, str]:
        return (owner.upper() if owner else None, table.upper())

    # ── Procedure / function calls ──────────────────────────────────────

    def call_procedure(self, cursor: Any, name: str, params: dict[str, Any], out_params: list[str]) -> dict[str, Any]:
        import oracledb

        kw_args: dict[str, Any] = dict(params)
        out_vars: dict[str, Any] = {}
        for op_name in out_params:
            var = cursor.var(oracledb.DB_TYPE_VARCHAR, 4000)
            kw_args[op_name] = var
            out_vars[op_name] = var
        cursor.callproc(name, keywordParameters=kw_args)
        return {k: self.convert_value(v.getvalue()) for k, v in out_vars.items()}

    def call_function(self, cursor: Any, name: str, params: dict[str, Any]) -> Any:
        import oracledb

        param_keys = sorted(params.keys())
        args = [params[k] for k in param_keys]
        return cursor.callfunc(name, oracledb.DB_TYPE_VARCHAR, args)

    # ── Type conversion ─────────────────────────────────────────────────

    def convert_value(self, value: Any) -> Any:
        try:
            import oracledb

            if isinstance(value, oracledb.LOB):
                return value.read()
        except ImportError:
            pass
        return value

    # ── DML bind placeholders ───────────────────────────────────────────

    def insert_bind_placeholder(self, col: str) -> str:
        return f":{col}"

    def where_bind_placeholder(self, param: str) -> str:
        return f":{param}"

    def table_name_regex(self) -> re.Pattern[str]:
        return _TABLE_RE

    # ── Health check ────────────────────────────────────────────────────

    def health_check_sql(self) -> str:
        return "SELECT 1 FROM DUAL"
