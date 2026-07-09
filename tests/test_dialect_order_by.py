"""Each dialect injects order_by at the engine-correct position; None = unchanged."""

from app.db.dialects.mssql_dialect import MssqlDialect
from app.db.dialects.oracle_dialect import OracleDialect
from app.db.dialects.postgresql_dialect import PostgresqlDialect

SQL = "SELECT a, b FROM t"


def _norm(s):
    return " ".join(s.split())


# ── backward compat: order_by=None reproduces existing SQL shape ──────────


def test_pg_paginated_no_order_unchanged():
    assert _norm(PostgresqlDialect().wrap_paginated(SQL)) == _norm(
        f"SELECT * FROM ({SQL}) sub_ LIMIT :pag_limit OFFSET :pag_offset"
    )


def test_mssql_paginated_no_order_uses_select_null():
    out = _norm(MssqlDialect().wrap_paginated(SQL))
    assert "ORDER BY (SELECT NULL)" in out


def test_oracle_paginated_no_order_has_no_inner_order():
    assert "ORDER BY" not in OracleDialect().wrap_paginated(SQL)


# ── order_by injected ─────────────────────────────────────────────────────


def test_pg_paginated_with_order():
    out = _norm(PostgresqlDialect().wrap_paginated(SQL, "NAME DESC"))
    assert _norm(f"SELECT * FROM ({SQL}) sub_ ORDER BY NAME DESC LIMIT :pag_limit OFFSET :pag_offset") == out


def test_pg_top_n_and_one_with_order():
    assert "ORDER BY NAME DESC" in PostgresqlDialect().wrap_top_n(SQL, "NAME DESC")
    assert "ORDER BY NAME DESC" in PostgresqlDialect().wrap_one(SQL, "NAME DESC")


def test_mssql_paginated_with_order_in_window():
    out = _norm(MssqlDialect().wrap_paginated(SQL, "NAME DESC"))
    assert "ROW_NUMBER() OVER (ORDER BY NAME DESC)" in out


def test_mssql_top_n_and_one_with_order():
    assert "ORDER BY NAME DESC" in MssqlDialect().wrap_top_n(SQL, "NAME DESC")
    assert "ORDER BY NAME DESC" in MssqlDialect().wrap_one(SQL, "NAME DESC")


def test_oracle_paginated_orders_before_rownum():
    out = _norm(OracleDialect().wrap_paginated(SQL, "NAME DESC"))
    # inner ordering applied before the ROWNUM filter that assigns pagination rows
    assert f"SELECT * FROM ({SQL}) ORDER BY NAME DESC" in out
    assert out.index("ORDER BY NAME DESC") < out.index("ROWNUM <=")


def test_oracle_top_n_and_one_with_order():
    assert "ORDER BY NAME DESC" in OracleDialect().wrap_top_n(SQL, "NAME DESC")
    assert "ORDER BY NAME DESC" in OracleDialect().wrap_one(SQL, "NAME DESC")


# ── wrap_all (base, shared by all dialects) ───────────────────────────────


def test_wrap_all_none_returns_sql_unchanged():
    assert PostgresqlDialect().wrap_all(SQL) == SQL


def test_wrap_all_with_order():
    assert _norm(PostgresqlDialect().wrap_all(SQL, "NAME DESC")) == _norm(
        f"SELECT * FROM ({SQL}) sub_ ORDER BY NAME DESC"
    )
