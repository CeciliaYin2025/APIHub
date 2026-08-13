"""GET /mcp/catalog-version — the fingerprint the MCP server polls.

It exists so a short MCP_CATALOG_TTL_SECONDS costs one small request instead of
a refetch of every definition on every alias. That only works if the hash is
**stable** when nothing tool-shaping changed and **moves** when something did;
either failure mode is bad in a different way. A hash that drifts on its own
makes the optimisation worthless (constant reloads); one that misses a change
serves a stale tool list forever, which is worse than what it replaced.
"""

import app.services.mcp_catalog_version as version_mod
from app.services.mcp_catalog_version import catalog_version
from app.services.sql_registry import ParamDef, SqlDefinition


def _defs(**overrides):
    base = {
        "name": "emps",
        "description": "Headcount",
        "sql": "SELECT 1",
        "permission": "query:emps",
        "db_alias": "dw",
        "group_name": "HR",
        "result_description": "EMP_NO, NAME",
        "params": {"since": ParamDef(name="since", type="date", required=True, description="From")},
    }
    base.update(overrides)
    return [SqlDefinition(**base)]


def _patch(monkeypatch, definitions, aliases=("dw",)):
    class _SqlRegistry:
        def list_all(self, db_alias=None):
            return definitions

    class _DbRegistry:
        def list_databases(self):
            return [{"alias": a} for a in aliases]

    monkeypatch.setattr(version_mod, "sql_registry", _SqlRegistry())
    monkeypatch.setattr(version_mod, "db_registry", _DbRegistry())


def test_is_stable_across_calls(monkeypatch):
    _patch(monkeypatch, _defs())
    assert catalog_version() == catalog_version()


def test_changes_when_a_tool_shaping_field_changes(monkeypatch):
    _patch(monkeypatch, _defs())
    baseline = catalog_version()

    for field, value in [
        ("name", "employees"),
        ("group_name", "Payroll"),
        ("description", "Something else"),
        ("result_description", "EMP_NO only"),
        ("permission", "query:other"),
        ("db_alias", "hrdc"),
    ]:
        _patch(monkeypatch, _defs(**{field: value}))
        assert catalog_version() != baseline, f"{field} must move the fingerprint"


def test_ignores_the_sql_body(monkeypatch):
    """An SQL edit changes results, not the tool — and is already live.

    Hashing it would force a full catalog rebuild on every query tweak while
    changing nothing an LLM can see.
    """
    _patch(monkeypatch, _defs())
    baseline = catalog_version()
    _patch(monkeypatch, _defs(sql="SELECT 2 FROM DUAL WHERE 1=1"))
    assert catalog_version() == baseline


def test_changes_when_a_parameter_declaration_changes(monkeypatch):
    _patch(monkeypatch, _defs())
    baseline = catalog_version()

    for param in [
        ParamDef(name="since", type="string", required=True, description="From"),
        ParamDef(name="since", type="date", required=False, description="From"),
        ParamDef(name="since", type="date", required=True, description="Different help text"),
        ParamDef(name="renamed", type="date", required=True, description="From"),
    ]:
        _patch(monkeypatch, _defs(params={param.name: param}))
        assert catalog_version() != baseline


def test_changes_when_a_definition_is_added_or_removed(monkeypatch):
    _patch(monkeypatch, _defs())
    baseline = catalog_version()

    _patch(monkeypatch, _defs() + _defs(name="other"))
    assert catalog_version() != baseline

    _patch(monkeypatch, [])
    assert catalog_version() != baseline


def test_changes_when_the_alias_list_changes(monkeypatch):
    """A new data source means new tools even with the same definitions."""
    _patch(monkeypatch, _defs(), aliases=("dw",))
    baseline = catalog_version()
    _patch(monkeypatch, _defs(), aliases=("dw", "hrdc"))
    assert catalog_version() != baseline


def test_is_order_independent(monkeypatch):
    """Registry iteration order is a dict detail, not a catalog change."""
    a, b = _defs()[0], _defs(name="other")[0]
    _patch(monkeypatch, [a, b])
    forward = catalog_version()
    _patch(monkeypatch, [b, a])
    assert catalog_version() == forward


def test_endpoint_is_open_to_any_authenticated_caller(monkeypatch):
    """Not superuser-gated, unlike /mcp/tools.

    Under OAuth the MCP server proxies as whoever is calling, so a superuser
    gate would make the endpoint useless to the one process it exists for. Safe
    because the response names nothing and counts nothing.
    """
    from fastapi.routing import APIRoute

    import app.api.v1.mcp as mcp_api
    from app.services.auth_service import get_current_active_superuser, get_current_user

    route = next(
        r for r in mcp_api.router.routes if isinstance(r, APIRoute) and r.endpoint is mcp_api.get_catalog_version
    )
    deps = [d.call for d in route.dependant.dependencies]
    assert get_current_user in deps
    assert get_current_active_superuser not in deps


def test_endpoint_returns_only_a_digest(monkeypatch):
    import app.api.v1.mcp as mcp_api

    monkeypatch.setattr(mcp_api, "catalog_version", lambda: "deadbeef")
    assert mcp_api.get_catalog_version(_user=object()) == {"version": "deadbeef"}


def test_route_is_registered_under_the_api_prefix():
    from app.core.config import settings
    from app.main import app

    assert f"{settings.api_prefix}/mcp/catalog-version" in app.openapi()["paths"]
