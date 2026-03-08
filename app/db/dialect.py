"""Abstract dialect interface encapsulating SQL/type differences across engines."""

from __future__ import annotations

import abc
import re
from typing import Any


class Dialect(abc.ABC):
    """Engine-specific SQL generation, type conversion, and introspection."""

    # ── Pagination ──────────────────────────────────────────────────────

    @abc.abstractmethod
    def wrap_paginated(self, sql: str) -> str:
        """Wrap *sql* with engine-specific pagination (uses bind params from ``pagination_binds``)."""

    @abc.abstractmethod
    def pagination_binds(self, offset: int, page_size: int) -> dict[str, Any]:
        """Return bind params dict for the paginated wrapper."""

    @abc.abstractmethod
    def wrap_top_n(self, sql: str) -> str:
        """Wrap *sql* to return at most N rows (uses bind from ``top_n_binds``)."""

    @abc.abstractmethod
    def top_n_binds(self, n: int) -> dict[str, Any]:
        """Return bind params for the top-N wrapper."""

    @abc.abstractmethod
    def wrap_one(self, sql: str) -> str:
        """Wrap *sql* to return at most 1 row."""

    @property
    @abc.abstractmethod
    def pagination_column(self) -> str | None:
        """Name of the synthetic pagination column to filter from results, or None."""

    # ── Count query ─────────────────────────────────────────────────────

    def wrap_count(self, sql: str) -> str:
        """Wrap *sql* in a COUNT(*) query."""
        return f"SELECT COUNT(*) FROM ({sql}) cnt_"

    # ── Bind parameter conversion ───────────────────────────────────────

    @abc.abstractmethod
    def convert_bind_params(self, sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Convert ``:name`` bind syntax to the engine-native format.

        Returns ``(converted_sql, converted_params)``.
        """

    # ── Schema introspection ────────────────────────────────────────────

    @abc.abstractmethod
    def get_columns_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
        """Return ``(sql, binds)`` to query column metadata."""

    @abc.abstractmethod
    def get_primary_key_sql(self, owner: str | None, table: str) -> tuple[str, dict[str, Any]]:
        """Return ``(sql, binds)`` to query primary key columns."""

    @abc.abstractmethod
    def parse_column_row(self, row: tuple) -> dict[str, Any]:
        """Parse a result row from ``get_columns_sql`` into a column metadata dict."""

    @abc.abstractmethod
    def normalize_table_parts(self, owner: str | None, table: str) -> tuple[str | None, str]:
        """Apply engine-specific casing to owner and table name."""

    # ── Procedure / function calls ──────────────────────────────────────

    @abc.abstractmethod
    def call_procedure(
        self, cursor: Any, name: str, params: dict[str, Any], out_params: list[str]
    ) -> dict[str, Any]:
        """Execute a stored procedure, return dict of OUT param values."""

    @abc.abstractmethod
    def call_function(self, cursor: Any, name: str, params: dict[str, Any]) -> Any:
        """Execute a stored function, return its result."""

    # ── Type / value conversion ─────────────────────────────────────────

    @abc.abstractmethod
    def convert_value(self, value: Any) -> Any:
        """Convert an engine-specific value (e.g. LOB) to a Python-native type."""

    # ── DML bind placeholders ───────────────────────────────────────────

    @abc.abstractmethod
    def insert_bind_placeholder(self, col: str) -> str:
        """Return the bind placeholder for an INSERT column (e.g. ``:col`` or ``%(col)s``)."""

    @abc.abstractmethod
    def where_bind_placeholder(self, param: str) -> str:
        """Return the bind placeholder for a WHERE parameter."""

    @abc.abstractmethod
    def table_name_regex(self) -> re.Pattern[str]:
        """Return a compiled regex for valid table names."""
