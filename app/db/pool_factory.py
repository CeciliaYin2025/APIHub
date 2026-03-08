"""Factory for creating engine-specific pool adapters."""

from __future__ import annotations

from app.db.pool_adapter import PoolAdapter
from app.db.types import DbType


def create_pool_adapter(db_type: DbType) -> PoolAdapter:
    """Return a new (unconfigured) pool adapter for the given engine type."""
    if db_type == "oracle":
        from app.db.adapters.oracle_adapter import OraclePoolAdapter

        return OraclePoolAdapter()
    elif db_type == "postgresql":
        from app.db.adapters.postgresql_adapter import PostgresPoolAdapter

        return PostgresPoolAdapter()
    elif db_type == "mssql":
        from app.db.adapters.mssql_adapter import MssqlPoolAdapter

        return MssqlPoolAdapter()
    else:
        raise ValueError(f"Unsupported database type: {db_type!r}")
