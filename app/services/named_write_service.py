"""Named write definitions: validate a call against its definition, then run it.

The generic ``/writes/batch`` takes the column list from the first row and the
``where`` from the caller, so a mistaken payload can write any column or delete
the whole table. Here both come from the definition:

- columns are an allowlist; anything undeclared is a 400;
- ``replace`` deletes only ``WHERE key_columns = key``, the key is mandatory,
  and every inserted row must carry that same key — so a call can only ever
  replace what an earlier call with the same key wrote (resending a file is
  idempotent, and rows other writers own are out of reach);
- values are coerced to driver-native types here, so dates no longer depend on
  the data source session's NLS_DATE_FORMAT.

SQL identifiers come only from the stored definition; request values are always
bound. Bind names are positional (``c0``, ``k0``) because a column may be called
something Oracle refuses as a bind variable name.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.db.oracle_pool import get_connection, get_dialect_for

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_DATE_RE = re.compile(r"^(\d{4})[-/](\d{2})[-/](\d{2})$")


class NamedWriteError(ValueError):
    """The request does not satisfy the definition (maps to HTTP 400)."""


class NamedWriteExecutionError(Exception):
    """The database rejected the write; the transaction was rolled back."""

    def __init__(self, phase: str, message: str):
        super().__init__(message)
        self.phase = phase


# ── Definition shape ─────────────────────────────────────────────────────


def validate_definition_shape(mode: str, columns: list[dict[str, Any]], key_columns: list[str]) -> None:
    """Rules that need no database. Raises ValueError."""
    if mode not in ("insert", "replace"):
        raise ValueError(f"mode must be 'insert' or 'replace', got {mode!r}")
    if not columns:
        raise ValueError("at least one column is required")
    seen: set[str] = set()
    for col in columns:
        name = col.get("name", "")
        if not _IDENT_RE.match(name):
            raise ValueError(f"invalid column name: {name!r}")
        if name.upper() in seen:
            raise ValueError(f"duplicate column: {name}")
        seen.add(name.upper())
    if mode == "replace":
        if not key_columns:
            raise ValueError("replace mode needs at least one key column")
        for k in key_columns:
            if k.upper() not in seen:
                raise ValueError(f"key column {k!r} is not a declared column")
        if len({k.upper() for k in key_columns}) != len(key_columns):
            raise ValueError("duplicate key column")
    elif key_columns:
        raise ValueError("insert mode does not take key columns")


def missing_table_columns(columns: list[dict[str, Any]], table_columns: list[str]) -> list[str]:
    """Declared columns the real table does not have (case-insensitive)."""
    actual = {c.upper() for c in table_columns}
    return [c["name"] for c in columns if c["name"].upper() not in actual]


def check_against_table(definition: Any) -> list[str]:
    """Describe the target table through the definition's alias.

    Returns the declared columns that do not exist. Raises ValueError when the
    table itself is not visible to that alias's database account.
    """
    from app.services.schema_service import describe_table

    described = describe_table(definition.table_name, db_alias=definition.db_alias)
    return missing_table_columns(definition.columns, [c["name"] for c in described["columns"]])


# ── Request validation ───────────────────────────────────────────────────


def _coerce(value: Any, col_type: str, where: str) -> Any:
    if value is None:
        return None
    if col_type == "string":
        if not isinstance(value, str):
            raise NamedWriteError(f"{where}: expected a string")
        return value
    if col_type == "number":
        if isinstance(value, bool):
            raise NamedWriteError(f"{where}: expected a number")
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            try:
                return Decimal(value.strip())
            except InvalidOperation:
                pass
        raise NamedWriteError(f"{where}: expected a number")
    if col_type == "date":
        m = _DATE_RE.match(value) if isinstance(value, str) else None
        if m:
            try:
                return date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                pass
        raise NamedWriteError(f"{where}: expected a date as YYYY-MM-DD or YYYY/MM/DD")
    if col_type == "datetime":
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.strip())
            except ValueError:
                pass
        raise NamedWriteError(f"{where}: expected an ISO 8601 datetime")
    raise NamedWriteError(f"{where}: unknown column type {col_type!r}")


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def prepare(definition: Any, key: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate *key*/*rows* against *definition*; return what execution needs.

    Returns ``{"columns": [declared names in insert order], "rows": [[values]],
    "key": [(declared name, value)]}``. Raises NamedWriteError.
    """
    by_upper = {c["name"].upper(): c for c in definition.columns}
    key_columns = [by_upper[k.upper()]["name"] for k in (definition.key_columns or [])]

    if len(rows) > definition.max_rows:
        raise NamedWriteError(f"too many rows: {len(rows)} > max_rows {definition.max_rows}")

    key_pairs: list[tuple[str, Any]] = []
    if definition.mode == "replace":
        if not key:
            raise NamedWriteError(f"key is required: {key_columns}")
        given = {k.upper(): v for k, v in key.items()}
        if set(given) != {k.upper() for k in key_columns}:
            raise NamedWriteError(f"key must have exactly {key_columns}, got {sorted(key)}")
        for name in key_columns:
            raw = given[name.upper()]
            if _is_blank(raw):
                raise NamedWriteError(f"key {name} must not be empty")
            key_pairs.append((name, _coerce(raw, by_upper[name.upper()]["type"], f"key {name}")))
    else:
        if key:
            raise NamedWriteError("insert mode does not take a key")
        if not rows:
            raise NamedWriteError("rows must not be empty")

    insert_cols: list[str] | None = None
    prepared_rows: list[list[Any]] = []
    key_values = {name.upper(): value for name, value in key_pairs}
    for i, row in enumerate(rows):
        normalized: dict[str, Any] = {}
        for raw_name, raw_value in row.items():
            col = by_upper.get(raw_name.upper())
            if col is None:
                raise NamedWriteError(f"row {i}: column {raw_name!r} is not declared in this definition")
            normalized[col["name"]] = _coerce(raw_value, col["type"], f"row {i} {col['name']}")

        for col in definition.columns:
            required = col.get("required") or col["name"] in key_columns
            if required and _is_blank(normalized.get(col["name"])):
                raise NamedWriteError(f"row {i}: {col['name']} is required")

        for name in key_columns:
            if normalized.get(name) != key_values[name.upper()]:
                raise NamedWriteError(f"row {i}: {name} must equal the request key {key_values[name.upper()]!r}")

        cols = [c["name"] for c in definition.columns if c["name"] in normalized]
        if insert_cols is None:
            insert_cols = cols
        elif cols != insert_cols:
            raise NamedWriteError(f"row {i}: has columns {cols}, but row 0 has {insert_cols}; all rows must match")
        prepared_rows.append([normalized[c] for c in cols])

    return {"columns": insert_cols or [], "rows": prepared_rows, "key": key_pairs}


# ── Execution ────────────────────────────────────────────────────────────


def build_statements(definition: Any, prepared: dict[str, Any], dialect: Any) -> dict[str, Any]:
    """Return ``{"delete": (sql, binds) | None, "insert": sql | None, "insert_binds": [...]}``."""
    table = definition.table_name
    regex = dialect.table_name_regex()
    if not regex.match(table):
        raise NamedWriteError(f"invalid table name in definition: {table}")

    delete = None
    if definition.mode == "replace":
        parts, binds = [], {}
        for i, (name, value) in enumerate(prepared["key"]):
            parts.append(f"{name} = {dialect.where_bind_placeholder(f'k{i}')}")
            binds[f"k{i}"] = value
        delete = (f"DELETE FROM {table} WHERE " + " AND ".join(parts), binds)

    insert, insert_binds = None, []
    if prepared["rows"]:
        cols = prepared["columns"]
        placeholders = ", ".join(dialect.insert_bind_placeholder(f"c{i}") for i in range(len(cols)))
        insert = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        insert_binds = [{f"c{i}": v for i, v in enumerate(values)} for values in prepared["rows"]]

    return {"delete": delete, "insert": insert, "insert_binds": insert_binds}


def execute_named_write(
    definition: Any, key: dict[str, Any] | None, rows: list[dict[str, Any]], dry_run: bool = False
) -> dict[str, Any]:
    """Validate and run one call in a single transaction (rolled back on dry_run)."""
    prepared = prepare(definition, key, rows)
    dialect = get_dialect_for(definition.db_alias)
    stmts = build_statements(definition, prepared, dialect)

    deleted = 0
    phase = "connect"
    with get_connection(definition.db_alias) as conn:
        cursor = conn.cursor()
        try:
            if stmts["delete"]:
                phase = "delete"
                sql, binds = stmts["delete"]
                cursor.execute(sql, binds)
                deleted = cursor.rowcount or 0
            if stmts["insert"]:
                phase = "insert"
                cursor.executemany(stmts["insert"], stmts["insert_binds"])
            phase = "commit"
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception as exc:
            conn.rollback()
            raise NamedWriteExecutionError(phase, str(exc)) from exc
        finally:
            cursor.close()

    return {
        "deleted_rows": deleted,
        # Each INSERT inserts exactly one row; executemany's rowcount is not
        # reported consistently across the three drivers.
        "inserted_rows": len(prepared["rows"]),
        "committed": not dry_run,
        "dry_run": dry_run,
    }
