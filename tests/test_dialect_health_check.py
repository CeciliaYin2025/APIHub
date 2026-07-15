"""Each dialect returns the engine-correct trivial connectivity probe."""

from app.db.dialects.mssql_dialect import MssqlDialect
from app.db.dialects.oracle_dialect import OracleDialect
from app.db.dialects.postgresql_dialect import PostgresqlDialect


def test_oracle_health_check_requires_dual():
    # Oracle has no bare SELECT — it needs a FROM clause.
    assert OracleDialect().health_check_sql() == "SELECT 1 FROM DUAL"


def test_postgresql_health_check_sql():
    assert PostgresqlDialect().health_check_sql() == "SELECT 1"


def test_mssql_health_check_sql():
    assert MssqlDialect().health_check_sql() == "SELECT 1"
