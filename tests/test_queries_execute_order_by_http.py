"""HTTP tests for `order_by` on POST /{db}/queries/execute.

Why the parameter exists: on 2026-09-16 the E&O agent was asked for the five
largest items in a period. `eo_detail` has no caller-facing ordering, so it
reached for `eo_item_diff` and set `compare_date` to the earliest period on
record, making the delta approximate the absolute amount. The ranking came out
right by luck; BU_NO and PID came from the wrong period, because that tool
reports attribution as the MIN across the two periods it compares.

Ordering must therefore happen in SQL, before paging — sorting the returned page
would rank 100 arbitrary rows out of 140,803 — and it must replace the
definition's own ordering rather than follow it.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import databases as databases_api
from app.api.v1 import queries as queries_api
from app.models.user import Permission, Role, User
from app.services.auth_service import get_current_user
from app.services.sql_registry import SqlDefinition

COLUMNS = ["Site", "Item_No", "AMT"]
ROW = {"Site": "SP", "Item_No": "549B0010MR0", "AMT": 198349363}


def _client(monkeypatch, user, *, definition_order=None, fail_with=None):
    calls: list[dict] = []

    def fake_execute_query(definition, params, fetch_mode, **kwargs):
        calls.append({"fetch_mode": fetch_mode, **kwargs})
        if fail_with is not None:
            raise fail_with
        return {"columns": list(COLUMNS), "data": [dict(ROW)], "total": 1, "page": 1, "page_size": 50}

    definition = SqlDefinition(
        name="eo_detail",
        description="",
        sql="SELECT * FROM detail",
        permission="query:eo_detail",
        order_by=definition_order or [{"column": "Site", "direction": "ASC"}],
    )
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: definition)
    monkeypatch.setattr(queries_api, "execute_query", fake_execute_query)
    monkeypatch.setattr(queries_api, "record_audit", lambda *a, **k: None)

    application = FastAPI()
    application.include_router(queries_api.router)
    application.dependency_overrides[get_current_user] = lambda: user
    application.dependency_overrides[databases_api.get_db_alias] = lambda: "DW"
    return TestClient(application), calls


def _user():
    role = Role(name="r", permissions=[Permission(resource="query:eo_*", action="read")])
    return User(username="u", is_active=True, is_superuser=False, roles=[role])


def _execute(client, **extra):
    return client.post("/DW/queries/execute", json={"query_name": "eo_detail", "params": {}, **extra})


def test_omitting_order_by_sends_nothing_and_keeps_the_definitions_own_order(monkeypatch):
    """The contract for every caller that predates the parameter."""
    client, calls = _client(monkeypatch, _user())
    assert _execute(client).status_code == 200
    assert calls[0]["order_by"] == []


def test_object_form_is_forwarded_normalised(monkeypatch):
    client, calls = _client(monkeypatch, _user())
    _execute(client, order_by=[{"column": "AMT", "direction": "desc"}])

    assert calls[0]["order_by"] == [{"column": "AMT", "direction": "DESC"}]


def test_string_shorthand_is_forwarded_normalised(monkeypatch):
    client, calls = _client(monkeypatch, _user())
    _execute(client, order_by=["-AMT", "Item_No"])

    assert calls[0]["order_by"] == [
        {"column": "AMT", "direction": "DESC"},
        {"column": "Item_No", "direction": "ASC"},
    ]


def test_an_injection_attempt_is_rejected_before_execution(monkeypatch):
    client, calls = _client(monkeypatch, _user())
    resp = _execute(client, order_by=["AMT; DROP TABLE users"])

    assert resp.status_code == 400
    assert calls == [], "nothing may reach the database"


def test_a_column_the_result_lacks_is_a_400_not_a_500(monkeypatch):
    # The Hub cannot know a definition's output columns without running it, so
    # the driver is what rejects the name; that is still the caller's mistake.
    client, _ = _client(monkeypatch, _user(), fail_with=Exception("Invalid column name 'NOPE'."))
    resp = _execute(client, order_by=["-NOPE"])

    assert resp.status_code == 400
    assert "NOPE" in resp.json()["detail"]


def test_an_unrelated_failure_is_still_a_500(monkeypatch):
    client, _ = _client(monkeypatch, _user(), fail_with=Exception("Timeout expired"))
    resp = _execute(client, order_by=["-AMT"])

    assert resp.status_code == 500


def test_ordering_composes_with_projection(monkeypatch):
    client, calls = _client(monkeypatch, _user())
    body = _execute(client, order_by=["-AMT"], columns=["Item_No", "AMT"]).json()

    assert calls[0]["order_by"] == [{"column": "AMT", "direction": "DESC"}]
    assert body["columns"] == ["Item_No", "AMT"], "projection still applies to the ordered result"
