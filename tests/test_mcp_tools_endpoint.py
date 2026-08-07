"""GET /mcp/tools — superuser gating, audit wiring, and route registration.

The service layer's own behaviour is covered by
tests/test_mcp_catalog_service.py. What matters here is that the endpoint is
reachable at the documented path, that it stays superuser-only (it spans every
alias, so it enumerates definitions the caller may hold no permission for), and
that it lands in the usage report as an admin action rather than as data
access.
"""

from fastapi.routing import APIRoute

import app.api.v1.mcp as mcp_api
from app.main import app
from app.services.auth_service import get_current_active_superuser


class _State:
    principal = None


class _Req:
    def __init__(self):
        self.state = _State()


def _route():
    return next(r for r in app.routes if isinstance(r, APIRoute) and r.path.endswith("/mcp/tools"))


def test_route_is_registered_under_the_api_prefix():
    from app.core.config import settings

    assert _route().path == f"{settings.api_prefix}/mcp/tools"
    assert "GET" in _route().methods


def test_endpoint_requires_a_superuser():
    dependencies = [d.call for d in _route().dependant.dependencies]
    assert get_current_active_superuser in dependencies


def test_returns_the_service_view(monkeypatch):
    sentinel = {"tools": [], "summary": {"definitions": 0}}
    monkeypatch.setattr(mcp_api, "build_tool_view", lambda: sentinel)
    assert mcp_api.list_mcp_tools(request=_Req(), _admin=object()) is sentinel


def test_records_an_admin_report_action(monkeypatch):
    monkeypatch.setattr(mcp_api, "build_tool_view", dict)
    req = _Req()
    mcp_api.list_mcp_tools(request=req, _admin=object())
    # action_type "report" keeps it out of the query/procedure/write volume the
    # usage report defaults to.
    assert req.state.audit_action_type == "report"
    assert req.state.audit_action == "mcp_tools"
