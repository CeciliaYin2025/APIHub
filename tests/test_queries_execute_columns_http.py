"""HTTP tests for `columns` on POST /{db}/queries/execute.

The parameter exists to keep wide rows out of an LLM's context. It is opt-in,
and the first test here is the one that matters most to the ~91k REST calls a
month that predate it: omitting `columns` must change nothing.

The other property under test is the interaction with the audience view.
Projection runs *after* masking, so a Sales-scoped caller asking for an MC-only
column gets the same 400 as someone with a typo — the parameter cannot be used
to probe what the view hid.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import databases as databases_api
from app.api.v1 import queries as queries_api
from app.models.user import Permission, Role, User
from app.services import query_service
from app.services.auth_service import get_current_user
from app.services.sql_registry import SqlDefinition

COLUMNS = ["OU", "BU_NO", "OBSOLETE_OH_ALL_AMT_TWD", "OH_unrecoverable", "SUMMARY_ID"]
ROW = {
    "OU": "SP",
    "BU_NO": "E1A",
    "OBSOLETE_OH_ALL_AMT_TWD": 107787895,
    "OH_unrecoverable": 42,
    "SUMMARY_ID": 1,
}


class _Dialect:
    def wrap_count(self, sql):
        return f"SELECT COUNT(*) FROM ({sql}) c"

    def wrap_paginated(self, sql, order_by=None):
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
    description = [(c,) for c in COLUMNS]

    def execute(self, sql, binds=None):
        pass

    def fetchone(self):
        return (1,)

    def close(self):
        pass

    def __iter__(self):
        return iter([tuple(ROW[c] for c in COLUMNS)])


class _Conn:
    def cursor(self):
        return _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client(monkeypatch, user, definition_name="eo_summary"):
    application = FastAPI()
    application.include_router(queries_api.router)
    application.dependency_overrides[get_current_user] = lambda: user
    application.dependency_overrides[databases_api.get_db_alias] = lambda: "DW"

    definition = SqlDefinition(
        name=definition_name,
        description="",
        sql="SELECT ep.* FROM INV_EO_PID_SUMMARY_F ep",
        permission=f"query:{definition_name}",
    )
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: definition)
    monkeypatch.setattr(query_service, "get_dialect_for", lambda alias: _Dialect())
    monkeypatch.setattr(query_service, "get_connection", lambda alias: _Conn())
    return TestClient(application)


def _user(*perms, superuser=False):
    role = Role(name="r", permissions=[Permission(resource=r, action=a) for r, a in perms])
    return User(username="u", is_active=True, is_superuser=superuser, roles=[role])


def _execute(client, name="eo_summary", **extra):
    return client.post("/DW/queries/execute", json={"query_name": name, "params": {}, **extra})


def test_omitting_columns_returns_every_column(monkeypatch):
    """The contract every pre-existing caller depends on."""
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    body = _execute(client).json()

    assert body["columns"] == COLUMNS
    assert body["data"] == [ROW]


def test_projection_narrows_rows_and_keeps_requested_order(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    body = _execute(client, columns=["BU_NO", "OU"]).json()

    assert body["columns"] == ["BU_NO", "OU"]
    assert body["data"] == [{"BU_NO": "E1A", "OU": "SP"}]


def test_matching_is_case_insensitive(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    body = _execute(client, columns=["ou", "summary_id"]).json()

    assert body["columns"] == ["OU", "SUMMARY_ID"]


def test_unknown_column_is_400_and_lists_what_is_available(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    resp = _execute(client, columns=["OU", "NOT_A_COLUMN"])

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "NOT_A_COLUMN" in detail and "BU_NO" in detail


def test_total_is_unaffected_by_projection(monkeypatch):
    """Projection is horizontal; the row count must not move."""
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    assert _execute(client, columns=["OU"]).json()["total"] == 1


def test_count_mode_ignores_columns(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    body = _execute(client, fetch_mode="count", columns=["OU"]).json()

    assert body["total"] == 1
    assert body["data"] == []


def test_projection_composes_with_the_sales_view(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    body = _execute(client, view="sales", columns=["OBSOLETE_OH_ALL_AMT_TWD"]).json()

    assert body["columns"] == ["OBSOLETE_OH_ALL_AMT_TWD"]
    assert body["view"] == "sales"
    assert set(body["masked_columns"]) == {"OH_unrecoverable", "SUMMARY_ID"}, (
        "masked_columns must still report what the VIEW removed, not what the projection dropped"
    )


def test_a_masked_column_cannot_be_recovered_through_columns(monkeypatch):
    """The security property: masked reads as unknown, not as forbidden."""
    client = _client(monkeypatch, _user(("query:eo_summary", "read"), ("eo:read:scoped", "read")))
    resp = _execute(client, columns=["OH_unrecoverable"])

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "OH_unrecoverable" in detail
    assert "SUMMARY_ID" not in detail, "the error must not enumerate columns the view hid"
