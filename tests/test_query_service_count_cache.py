"""page/batch reuse a cached COUNT(*); count/all always read live.

This is the fix for the paging walk that re-ran the same count once per page —
439 identical counts for one unfiltered eo_detail walk on 2026-08-24.
"""

from app.core.config import settings
from app.services import count_cache, query_service
from app.services.sql_registry import SqlDefinition


class _Dialect:
    def wrap_count(self, sql):
        return f"SELECT COUNT(*) FROM ({sql}) c"

    def wrap_paginated(self, sql, order_by=None):
        return f"{sql} /*paged*/"

    def wrap_all(self, sql, order_by=None):
        return sql

    def pagination_binds(self, offset, page_size):
        return {}

    @property
    def pagination_column(self):
        return None

    def convert_bind_params(self, sql, binds):
        return sql, binds

    def convert_value(self, v):
        return v


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = [("a",)]

    def execute(self, sql, binds=None):
        self._conn.executed.append(sql)

    def fetchone(self):
        return (4821,)

    def close(self):
        pass

    def __iter__(self):
        return iter([])


class _Conn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return _Cursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def counts_run(self):
        return [s for s in self.executed if s.startswith("SELECT COUNT(*)")]


def _patch(monkeypatch, conn):
    monkeypatch.setattr(query_service, "get_dialect_for", lambda alias: _Dialect())
    monkeypatch.setattr(query_service, "get_connection", lambda alias: conn)


def _defn(name="q", sql="SELECT a FROM t"):
    return SqlDefinition(name=name, description="", sql=sql, permission=f"query:{name}", order_by=[])


def test_second_page_reuses_the_count(monkeypatch):
    conn = _Conn()
    _patch(monkeypatch, conn)

    first = query_service.execute_query(_defn(), {}, fetch_mode="page", page=1, page_size=100)
    second = query_service.execute_query(_defn(), {}, fetch_mode="page", page=2, page_size=100)

    assert first["total"] == second["total"] == 4821
    assert len(conn.counts_run()) == 1, "page 2 must not recount"


def test_paging_walk_runs_one_count_not_one_per_page(monkeypatch):
    conn = _Conn()
    _patch(monkeypatch, conn)

    for page in range(1, 11):
        query_service.execute_query(_defn(), {}, fetch_mode="page", page=page, page_size=100)

    assert len(conn.counts_run()) == 1
    assert len(conn.executed) == 11  # 1 count + 10 page queries


def test_different_binds_recount(monkeypatch):
    conn = _Conn()
    _patch(monkeypatch, conn)
    defn = SqlDefinition(
        name="q",
        description="",
        sql="SELECT a FROM t WHERE ou = :ou",
        permission="query:q",
        params={"ou": {"type": "string", "required": False}},
        order_by=[],
    )

    query_service.execute_query(defn, {"ou": "SP"}, fetch_mode="page", page=1)
    query_service.execute_query(defn, {"ou": "SC"}, fetch_mode="page", page=1)

    assert len(conn.counts_run()) == 2


def test_batch_mode_also_reuses(monkeypatch):
    conn = _Conn()
    _patch(monkeypatch, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="batch", batch_size=5000, batch_offset=0)
    query_service.execute_query(_defn(), {}, fetch_mode="batch", batch_size=5000, batch_offset=5000)

    assert len(conn.counts_run()) == 1


def test_batch_has_more_still_correct_from_cached_total(monkeypatch):
    conn = _Conn()
    _patch(monkeypatch, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="batch", batch_size=5000, batch_offset=0)
    last = query_service.execute_query(_defn(), {}, fetch_mode="batch", batch_size=5000, batch_offset=4000)

    assert last["total"] == 4821
    assert last["has_more"] is False


def test_count_mode_never_serves_from_cache(monkeypatch):
    """`count` mode's answer IS the count, so it must be live."""
    conn = _Conn()
    _patch(monkeypatch, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="page", page=1)
    query_service.execute_query(_defn(), {}, fetch_mode="count")

    assert len(conn.counts_run()) == 2


def test_count_mode_does_not_populate_the_cache(monkeypatch):
    conn = _Conn()
    _patch(monkeypatch, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="count")
    assert count_cache.get(None, "SELECT COUNT(*) FROM (SELECT a FROM t) c", {}) is None


def test_all_mode_stays_live(monkeypatch):
    conn = _Conn()
    _patch(monkeypatch, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="page", page=1)
    query_service.execute_query(_defn(), {}, fetch_mode="all")

    assert len(conn.counts_run()) == 2


def test_disabled_ttl_recounts_every_page(monkeypatch):
    monkeypatch.setattr(settings, "query_count_cache_ttl_seconds", 0)
    conn = _Conn()
    _patch(monkeypatch, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="page", page=1)
    query_service.execute_query(_defn(), {}, fetch_mode="page", page=2)

    assert len(conn.counts_run()) == 2
