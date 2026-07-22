"""HTTP-level test for fetch_mode=count on POST /{db}/queries/execute.

Exercises the real request path — QueryRequest validation, the execute()
router handler, execute_query's dispatch, and _fetch_count — with only the
low-level DB connection/dialect faked (same fake-dialect/fake-connection
technique as tests/test_query_service_count.py).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import databases as databases_api
from app.api.v1 import queries as queries_api
from app.models.user import User
from app.services import query_service
from app.services.auth_service import get_current_user
from app.services.sql_registry import SqlDefinition


class _Dialect:
    def __init__(self, total):
        self._total = total

    def wrap_count(self, sql):
        return f"SELECT COUNT(*) FROM ({sql}) c"

    def convert_bind_params(self, sql, binds):
        return sql, binds

    def convert_value(self, v):
        return v


class _Cursor:
    def __init__(self, total):
        self._total = total

    def execute(self, sql, binds=None):
        pass

    def fetchone(self):
        return (self._total,)

    def close(self):
        pass


class _Conn:
    def __init__(self, total):
        self._total = total

    def cursor(self):
        return _Cursor(self._total)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client(monkeypatch, total=4821):
    application = FastAPI()
    application.include_router(queries_api.router)

    admin = User(username="admin", is_superuser=True, is_active=True)
    application.dependency_overrides[get_current_user] = lambda: admin
    application.dependency_overrides[databases_api.get_db_alias] = lambda: "hr"

    definition = SqlDefinition(
        name="list_employees", description="", sql="SELECT * FROM employees", permission="query:list_employees"
    )
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: definition)
    monkeypatch.setattr(query_service, "get_dialect_for", lambda alias: _Dialect(total))
    monkeypatch.setattr(query_service, "get_connection", lambda alias: _Conn(total))

    return TestClient(application)


def test_count_mode_returns_200_with_documented_shape(monkeypatch):
    client = _client(monkeypatch, total=4821)

    response = client.post(
        "/hr/queries/execute",
        json={"query_name": "list_employees", "params": {}, "fetch_mode": "count"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "query_name": "list_employees",
        "fetch_mode": "count",
        "total": 4821,
        "page": None,
        "page_size": None,
        "batch_offset": None,
        "batch_size": None,
        "has_more": None,
        "columns": [],
        "data": [],
    }


def test_invalid_fetch_mode_still_rejected(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/hr/queries/execute",
        json={"query_name": "list_employees", "params": {}, "fetch_mode": "bogus"},
    )

    assert response.status_code == 422
