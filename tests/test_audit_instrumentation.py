"""Routers hand the usage report its action_type / params / result_rows.

These call the route functions directly with fakes: the point is the
record_audit contract, not HTTP plumbing (covered by tests/e2e).
"""

import pytest
from fastapi import HTTPException

import app.api.v1.queries as queries_api
from app.schemas.query import QueryRequest


class _State:
    principal = None


class _Req:
    def __init__(self):
        self.state = _State()


class _Def:
    permission = "query:get_orders"


def test_query_execute_records_type_params_and_rows(monkeypatch):
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: _Def())
    monkeypatch.setattr(queries_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(queries_api, "execute_query", lambda *a, **k: {
        "total": 99, "columns": ["id"], "data": [{"id": 1}, {"id": 2}],
    })

    req = _Req()
    payload = QueryRequest(query_name="get_orders", params={"dept": "IT"}, fetch_mode="page")
    queries_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "query"
    assert req.state.audit_action == "get_orders"
    assert req.state.audit_params == {"params": {"dept": "IT"}, "fetch_mode": "page"}
    # page mode reports the rows actually returned, not the grand total
    assert req.state.audit_result_rows == 2


def test_query_execute_count_mode_records_total(monkeypatch):
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: _Def())
    monkeypatch.setattr(queries_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(queries_api, "execute_query", lambda *a, **k: {
        "total": 4321, "columns": [], "data": [],
    })

    req = _Req()
    payload = QueryRequest(query_name="get_orders", fetch_mode="count")
    queries_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_result_rows == 4321


def test_query_execute_records_params_even_when_definition_missing(monkeypatch):
    # A 404 still has to leave an audited row: "why does this query keep
    # failing" is the main thing the detail view is for.
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: None)

    req = _Req()
    payload = QueryRequest(query_name="ghost", params={"a": 1})
    with pytest.raises(HTTPException) as excinfo:
        queries_api.execute(req, payload, db="dw1", current_user=object())

    assert excinfo.value.status_code == 404
    assert req.state.audit_action_type == "query"
    assert req.state.audit_params == {"params": {"a": 1}, "fetch_mode": "page"}


def test_procedure_execute_records_type_and_params(monkeypatch):
    import app.api.v1.procedures as procedures_api
    import app.services.procedure_service as ps
    from app.schemas.query import ProcedureRequest

    monkeypatch.setattr(procedures_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(ps, "execute_procedure", lambda **k: {"result": None, "out_params": {}})

    req = _Req()
    payload = ProcedureRequest(name="PKG.REBUILD", params={"day": "2026-07-30"}, out_params=["rc"])
    procedures_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "procedure"
    assert req.state.audit_action == "PKG.REBUILD"
    assert req.state.audit_params == {
        "params": {"day": "2026-07-30"}, "out_params": ["rc"], "is_function": False,
    }


def test_write_execute_records_table_and_affected_rows(monkeypatch):
    import app.api.v1.writes as writes_api
    from app.schemas.query import WriteRequest

    monkeypatch.setattr(writes_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(writes_api, "execute_write", lambda **k: 3)

    req = _Req()
    payload = WriteRequest(operation="insert", table="ORDERS", data=[{"id": 1}])
    writes_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "write"
    assert req.state.audit_action == "ORDERS"
    assert req.state.audit_params == {
        "operation": "insert", "data": [{"id": 1}], "where": None, "async_mode": False,
    }
    assert req.state.audit_result_rows == 3


def test_batch_write_records_operations_and_total_affected(monkeypatch):
    import app.api.v1.writes as writes_api
    from app.schemas.query import BatchOperation, BatchWriteRequest

    monkeypatch.setattr(writes_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(writes_api, "execute_batch_write", lambda **k: {
        "total_affected": 5, "operation_count": 2, "committed": True, "results": [],
    })

    req = _Req()
    payload = BatchWriteRequest(table="ORDERS", operations=[
        BatchOperation(operation="insert", data=[{"id": 1}]),
        BatchOperation(operation="delete", where={"id": 2}),
    ])
    writes_api.batch_execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "write"
    assert req.state.audit_action == "ORDERS"
    assert req.state.audit_params["async_mode"] is False
    assert len(req.state.audit_params["operations"]) == 2
    assert req.state.audit_result_rows == 5
