"""execute_query(fetch_mode="count") returns only the row count, no data query."""

from app.services import query_service
from app.services.sql_registry import SqlDefinition


class _Dialect:
    """Records which wrap_* methods are called; wrap_count always available (base Dialect provides it)."""

    def __init__(self, count_value):
        self._count_value = count_value
        self.calls = []

    def wrap_count(self, sql):
        self.calls.append("wrap_count")
        return f"SELECT COUNT(*) FROM ({sql}) c"

    def wrap_paginated(self, sql, order_by=None):
        self.calls.append("wrap_paginated")
        return sql

    def wrap_all(self, sql, order_by=None):
        self.calls.append("wrap_all")
        return sql

    def wrap_top_n(self, sql, order_by=None):
        self.calls.append("wrap_top_n")
        return sql

    def wrap_one(self, sql, order_by=None):
        self.calls.append("wrap_one")
        return sql

    def pagination_binds(self, offset, page_size):
        return {}

    def top_n_binds(self, n):
        return {}

    @property
    def pagination_column(self):
        return None

    def convert_bind_params(self, sql, binds):
        return sql, binds

    def convert_value(self, v):
        return v

    def _count_row(self):
        return (self._count_value,)


class _Cursor:
    def __init__(self, dialect):
        self._dialect = dialect
        self.executed_sql = []
        self.description = [("a",)]

    def execute(self, sql, binds=None):
        self.executed_sql.append(sql)

    def fetchone(self):
        return self._dialect._count_row()

    def close(self):
        pass

    def __iter__(self):
        return iter([])


class _Conn:
    def __init__(self, dialect):
        self._dialect = dialect
        self.cursors = []

    def cursor(self):
        c = _Cursor(self._dialect)
        self.cursors.append(c)
        return c

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, dialect, conn):
    monkeypatch.setattr(query_service, "get_dialect_for", lambda alias: dialect)
    monkeypatch.setattr(query_service, "get_connection", lambda alias: conn)


def _defn():
    return SqlDefinition(name="q", description="", sql="SELECT a FROM t", permission="query:q", order_by=[])


def test_count_mode_returns_total_only(monkeypatch):
    dialect = _Dialect(count_value=4821)
    conn = _Conn(dialect)
    _patch(monkeypatch, dialect, conn)

    result = query_service.execute_query(_defn(), {}, fetch_mode="count")

    assert result == {"columns": [], "data": [], "total": 4821}


def test_count_mode_only_calls_wrap_count(monkeypatch):
    dialect = _Dialect(count_value=0)
    conn = _Conn(dialect)
    _patch(monkeypatch, dialect, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="count")

    assert dialect.calls == ["wrap_count"]


def test_count_mode_runs_exactly_one_query(monkeypatch):
    dialect = _Dialect(count_value=7)
    conn = _Conn(dialect)
    _patch(monkeypatch, dialect, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="count")

    assert len(conn.cursors) == 1
    assert len(conn.cursors[0].executed_sql) == 1
