"""AuditMiddleware records principal/action and never breaks the request."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.middleware.audit as au
from app.middleware.audit import AuditMiddleware, _should_skip, build_audit_row
from app.models.user import User
from app.services.auth_service import Principal


class _Resp:
    status_code = 200


class _State:
    principal = Principal(type="api_key", user=User(username="svc"), key_id=3, key_name="k")
    audit_db_alias = "hr"
    audit_action = "HR_QueryEmployeeAll"


class _Client:
    host = "10.0.0.9"


class _Req:
    method = "POST"
    url = type("U", (), {"path": "/api/v1/hr/queries/execute"})()
    client = _Client()
    state = _State()


def test_build_audit_row_fields():
    row = build_audit_row(_Req(), _Resp(), 42)
    assert row["principal_type"] == "api_key"
    assert row["principal"] == "svc"
    assert row["api_key_id"] == 3
    assert row["db_alias"] == "hr"
    assert row["action"] == "HR_QueryEmployeeAll"
    assert row["status_code"] == 200
    assert row["duration_ms"] == 42


def test_anonymous_when_no_principal():
    req = _Req()
    req.state = type("S", (), {"principal": None})()
    row = build_audit_row(req, _Resp(), 1)
    assert row["principal_type"] == "anonymous"
    assert row["principal"] == "anonymous"


def test_request_succeeds_even_if_audit_write_raises(monkeypatch):
    # write_audit is invoked as a BackgroundTask after the response is built;
    # a raise there must never surface to the client (swallowed by the
    # background-task wrapper / dispatch try-except).
    def _boom(row):
        raise RuntimeError("db down")

    monkeypatch.setattr(au, "write_audit", _boom)
    monkeypatch.setattr(au, "_should_skip", lambda path: False)

    application = FastAPI()
    application.add_middleware(AuditMiddleware)

    @application.get("/api/v1/ok")
    def ok():
        return {"ok": True}

    resp = TestClient(application).get("/api/v1/ok")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_write_audit_swallows_db_failure(monkeypatch):
    class _FakeSession:
        def add(self, _obj):
            pass

        def commit(self):
            raise RuntimeError("db down")

        def close(self):
            pass

    monkeypatch.setattr(au, "SessionLocal", lambda: _FakeSession())

    row = {
        "principal_type": "anonymous",
        "principal": "anonymous",
        "api_key_id": None,
        "api_key_name": None,
        "client_ip": "127.0.0.1",
        "method": "GET",
        "path": "/api/v1/ok",
        "db_alias": None,
        "action": None,
        "status_code": 200,
        "duration_ms": 1,
    }

    # Must not raise.
    au.write_audit(row)


def test_background_audit_write_runs_after_response(monkeypatch):
    # Proves the DB write actually happens (via BackgroundTask under
    # BaseHTTPMiddleware, or the threadpool fallback) even though it is not
    # awaited inline in dispatch.
    calls = []
    monkeypatch.setattr(au, "write_audit", lambda row: calls.append(row))
    monkeypatch.setattr(au, "_should_skip", lambda path: False)

    application = FastAPI()
    application.add_middleware(AuditMiddleware)

    @application.get("/api/v1/ok")
    def ok():
        return {"ok": True}

    resp = TestClient(application).get("/api/v1/ok")
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["path"] == "/api/v1/ok"


def test_should_skip_audits_normal_api_call():
    assert _should_skip("/api/v1/hr/queries/execute") is False


def test_should_skip_audits_business_path_containing_admin():
    # Regression: a db alias/query segment merely containing "admin" must
    # still be audited — only the Admin SPA itself is excluded.
    assert _should_skip("/api/v1/hr/admin_reports") is False


def test_should_skip_skips_admin_spa():
    assert _should_skip("/admin") is True
    assert _should_skip("/admin/api-keys") is True


def test_should_skip_skips_health_and_token():
    assert _should_skip("/api/v1/health") is True
    assert _should_skip("/api/v1/auth/token") is True
