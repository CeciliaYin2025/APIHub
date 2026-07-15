"""GET /databases/status is registered, superuser-gated, and returns service output."""

from app.api.v1 import databases as db_mod
from app.services.auth_service import get_current_active_superuser


def _route(path):
    # APIRouter bakes its prefix into route.path, so the full path is
    # "/databases/status" even though the decorator says "/status".
    return next(r for r in db_mod.router.routes if r.path == path)


def test_status_route_registered():
    assert "GET" in _route("/databases/status").methods


def test_status_route_requires_superuser():
    deps = _route("/databases/status").dependant.dependencies
    # The check opens real connections and returns raw driver errors, so it
    # must be superuser-gated, not merely authenticated.
    assert any(d.call is get_current_active_superuser for d in deps)


def test_database_status_returns_service_results(monkeypatch):
    payload = [{"alias": "a", "status": "online", "latency_ms": 1.0, "error": None, "checked_at": "t"}]
    monkeypatch.setattr(db_mod, "check_all_databases", lambda: payload)

    assert db_mod.database_status(_admin=None) == payload
