"""HTTP-level tests for the MC / Sales view on POST /{db}/queries/execute.

Exercises the real request path — QueryRequest validation, the router handler,
the projection and the permission narrowing — with only the DB connection and
dialect faked (same technique as tests/test_queries_execute_count_http.py).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import databases as databases_api
from app.api.v1 import queries as queries_api
from app.models.user import Permission, Role, User
from app.services import query_service
from app.services.auth_service import get_current_user
from app.services.sql_registry import SqlDefinition

# A row shaped like the real eo_summary: shared columns, an MC-only loss column,
# an MC-only excess column, and a column the dictionary does not mention at all.
COLUMNS = [
    "OU",
    "BU_NO",
    "PID",
    "OBSOLETE_OH_ALL_AMT_TWD",
    "OH_unrecoverable",
    "EXCESS_OHOPO_AMT_52W_TWD",
    "SUMMARY_ID",
]
ROW = {
    "OU": "SP",
    "BU_NO": "E1A",
    "PID": "DMN",
    "OBSOLETE_OH_ALL_AMT_TWD": 107787895,
    "OH_unrecoverable": 42,
    "EXCESS_OHOPO_AMT_52W_TWD": 7,
    "SUMMARY_ID": 1,
}
SALES_COLUMNS = ["OU", "BU_NO", "PID", "OBSOLETE_OH_ALL_AMT_TWD"]


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


# ── the view parameter ───────────────────────────────────────────────────────


def test_default_is_the_full_mc_view(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    body = _execute(client).json()

    assert body["columns"] == COLUMNS
    assert body["view"] == "mc"
    assert body["view_downgraded"] is False
    assert "masked_columns" not in body or body["masked_columns"] is None


def test_sales_view_drops_the_mc_only_columns(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    body = _execute(client, view="sales").json()

    assert body["columns"] == SALES_COLUMNS
    assert body["data"] == [{c: ROW[c] for c in SALES_COLUMNS}]
    assert set(body["masked_columns"]) == {"OH_unrecoverable", "EXCESS_OHOPO_AMT_52W_TWD", "SUMMARY_ID"}
    assert body["view"] == "sales"
    assert body["view_downgraded"] is False


def test_total_still_reports_matching_rows_not_kept_columns(monkeypatch):
    """Masking is horizontal; it must not touch the row count."""
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    assert _execute(client, view="sales").json()["total"] == 1


def test_unknown_view_is_rejected_by_validation(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read")))
    assert _execute(client, view="finance").status_code == 422


# ── permission narrowing ─────────────────────────────────────────────────────


def test_scoped_caller_gets_sales_even_when_asking_for_mc(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read"), ("eo:read:scoped", "read")))
    body = _execute(client, view="mc").json()

    assert body["columns"] == SALES_COLUMNS
    assert body["view"] == "sales"
    assert body["view_downgraded"] is True, "the narrowing must be reported, not silent"


def test_scoped_caller_defaults_to_sales(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_summary", "read"), ("eo:read:scoped", "read")))
    assert _execute(client).json()["columns"] == SALES_COLUMNS


def test_scoped_caller_cannot_reach_loss_figures_in_the_body(monkeypatch):
    """The point of the whole exercise."""
    client = _client(monkeypatch, _user(("query:eo_summary", "read"), ("eo:read:scoped", "read")))
    body = _execute(client, view="mc").json()

    assert "OH_unrecoverable" not in body["columns"]
    assert all("OH_unrecoverable" not in row for row in body["data"])


def test_full_grant_restores_the_mc_view(monkeypatch):
    client = _client(
        monkeypatch,
        _user(("query:eo_summary", "read"), ("eo:read:scoped", "read"), ("eo:read:full", "read")),
    )
    assert _execute(client, view="mc").json()["columns"] == COLUMNS


# ── everything else is untouched ─────────────────────────────────────────────


def test_definition_without_a_view_is_not_masked(monkeypatch):
    client = _client(monkeypatch, _user(("query:eo_detail", "read")), definition_name="eo_detail")
    body = _execute(client, "eo_detail", view="sales").json()

    assert body["columns"] == COLUMNS


def test_definition_without_a_view_omits_the_view_keys_entirely(monkeypatch):
    """Adding null keys would change the response shape of every endpoint."""
    client = _client(monkeypatch, _user(("query:eo_detail", "read")), definition_name="eo_detail")
    body = _execute(client, "eo_detail").json()

    assert "view" not in body
    assert "view_downgraded" not in body
    assert "masked_columns" not in body


def test_scoped_caller_is_not_narrowed_on_other_definitions(monkeypatch):
    """eo:read:scoped governs eo_summary; it is not a global mask."""
    client = _client(
        monkeypatch,
        _user(("query:eo_detail", "read"), ("eo:read:scoped", "read")),
        definition_name="eo_detail",
    )
    assert _execute(client, "eo_detail").json()["columns"] == COLUMNS
