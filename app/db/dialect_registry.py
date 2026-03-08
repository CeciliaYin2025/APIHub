"""Dialect factory and singleton cache."""

from __future__ import annotations

from app.db.dialect import Dialect
from app.db.types import DbType

_cache: dict[DbType, Dialect] = {}


def get_dialect(db_type: DbType) -> Dialect:
    """Return a cached dialect singleton for the given engine type."""
    if db_type in _cache:
        return _cache[db_type]

    if db_type == "oracle":
        from app.db.dialects.oracle_dialect import OracleDialect

        dialect: Dialect = OracleDialect()
    elif db_type == "postgresql":
        from app.db.dialects.postgresql_dialect import PostgresqlDialect

        dialect = PostgresqlDialect()
    elif db_type == "mssql":
        from app.db.dialects.mssql_dialect import MssqlDialect

        dialect = MssqlDialect()
    else:
        raise ValueError(f"Unsupported database type: {db_type!r}")

    _cache[db_type] = dialect
    return dialect
