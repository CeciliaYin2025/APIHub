"""execute_query passes the built order_by into the dialect wrap functions."""

from app.services import query_service
from app.services.sql_registry import SqlDefinition


class _Dialect:
    """Records the order_by argument each wrap call receives."""

    def __init__(self):
        self.calls = {}

    def wrap_count(self, sql):
        return f"SELECT COUNT(*) FROM ({sql}) c"

    def wrap_paginated(self, sql, order_by=None):
        self.calls["paginated"] = order_by
        return sql

    def wrap_all(self, sql, order_by=None):
        self.calls["all"] = order_by
        return sql

    def wrap_top_n(self, sql, order_by=None):
        self.calls["top_n"] = order_by
        return sql

    def wrap_one(self, sql, order_by=None):
        self.calls["one"] = order_by
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


class _Cursor:
    description = [("a",)]

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return (0,)

    def close(self):
        pass

    def __iter__(self):
        return iter([])


class _Conn:
    def cursor(self):
        return _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, dialect):
    monkeypatch.setattr(query_service, "get_dialect_for", lambda alias: dialect)
    monkeypatch.setattr(query_service, "get_connection", lambda alias: _Conn())


def _defn(order_by):
    return SqlDefinition(name="q", description="", sql="SELECT a FROM t", permission="query:q", order_by=order_by)


def test_page_mode_passes_order_by(monkeypatch):
    d = _Dialect()
    _patch(monkeypatch, d)
    query_service.execute_query(_defn([{"column": "a", "direction": "DESC"}]), {}, fetch_mode="page")
    assert d.calls["paginated"] == "a DESC"


def test_all_mode_uses_wrap_all_with_order(monkeypatch):
    d = _Dialect()
    _patch(monkeypatch, d)
    query_service.execute_query(_defn([{"column": "a"}]), {}, fetch_mode="all")
    assert d.calls["all"] == "a ASC"


def test_empty_order_by_passes_none(monkeypatch):
    d = _Dialect()
    _patch(monkeypatch, d)
    query_service.execute_query(_defn([]), {}, fetch_mode="top_n")
    assert d.calls["top_n"] is None
