"""The per-definition ``enabled`` switch.

Disabling takes a definition out of service without deleting it: it drops out of
the definition list and therefore out of the MCP tool catalog, and execution
refuses it. It stays in the registry so the Admin UI can switch it back on and
so execution can report "disabled" rather than the indistinguishable "not found".

The distinction those two error messages draw is the whole point of keeping
disabled definitions loaded, so it is asserted here rather than left to reading.
"""

import pytest
from fastapi import HTTPException

from app.services.sql_registry import SqlDefinition, SqlRegistry, _key


def _defn(name, alias="DW", enabled=True):
    return SqlDefinition(
        name=name,
        description="",
        sql="SELECT 1",
        permission=f"query:{name}",
        db_alias=alias,
        enabled=enabled,
    )


def _registry(*definitions):
    reg = SqlRegistry()
    for d in definitions:
        reg._definitions[_key(d.name, d.db_alias)] = d
    return reg


# ── registry ────────────────────────────────────────────────────────────────


def test_definitions_default_to_enabled():
    """Nothing is disabled by accident — the migration relies on this default."""
    assert _defn("q").enabled is True


def test_list_all_hides_disabled_by_default():
    reg = _registry(_defn("on"), _defn("off", enabled=False))
    assert [d.name for d in reg.list_all(db_alias="DW")] == ["on"]


def test_list_all_can_include_disabled():
    reg = _registry(_defn("on"), _defn("off", enabled=False))
    assert sorted(d.name for d in reg.list_all(db_alias="DW", include_disabled=True)) == ["off", "on"]


def test_get_still_returns_a_disabled_definition():
    """Callers need it back to report *why* it is unavailable."""
    reg = _registry(_defn("off", enabled=False))
    got = reg.get("off", db_alias="DW")
    assert got is not None and got.enabled is False


def test_disabled_is_hidden_per_alias():
    reg = _registry(_defn("q", "DW"), _defn("q", "HRDC", enabled=False))
    assert [d.db_alias for d in reg.list_all()] == ["DW"]
    assert len(reg.list_all(include_disabled=True)) == 2


# ── execution ───────────────────────────────────────────────────────────────


def _execute(monkeypatch, definition):
    """Drive the execute endpoint far enough to reach the enabled check."""
    import app.api.v1.queries as queries_api
    from app.schemas.query import QueryRequest

    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: definition)
    monkeypatch.setattr(queries_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(queries_api, "execute_query", lambda *a, **k: {"total": 0, "columns": [], "data": []})
    monkeypatch.setattr(queries_api, "record_audit", lambda *a, **k: None)

    class _Req:
        class state:
            pass

    return queries_api.execute(
        _Req(), QueryRequest(query_name="q", params={}, fetch_mode="page"), db="DW", current_user=object()
    )


def test_executing_a_disabled_definition_is_refused(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _execute(monkeypatch, _defn("q", enabled=False))
    assert exc.value.status_code == 404
    assert "disabled" in exc.value.detail


def test_the_refusal_is_distinct_from_not_found(monkeypatch):
    """'disabled' and 'not found' must not read the same to an operator."""
    with pytest.raises(HTTPException) as disabled:
        _execute(monkeypatch, _defn("q", enabled=False))
    with pytest.raises(HTTPException) as missing:
        _execute(monkeypatch, None)
    assert disabled.value.detail != missing.value.detail
    assert "not found" in missing.value.detail


def test_an_enabled_definition_still_executes(monkeypatch):
    """The guard must not become a blanket refusal."""
    result = _execute(monkeypatch, _defn("q"))
    assert result.total == 0


# ── MCP catalog view ────────────────────────────────────────────────────────


def _view(monkeypatch, definitions):
    import app.services.mcp_catalog_service as mod

    class _FakeDb:
        def list_databases(self):
            return [{"alias": "DW"}]

    class _FakeReg:
        def list_all(self, db_alias=None, include_disabled=False):
            return definitions if include_disabled else [d for d in definitions if d.enabled]

    monkeypatch.setattr(mod, "db_registry", _FakeDb())
    monkeypatch.setattr(mod, "sql_registry", _FakeReg())
    return mod.build_tool_view()


def test_catalog_view_reports_a_disabled_definition_as_unexposed(monkeypatch):
    """It must still appear: the view exists to explain why a tool is missing."""
    view = _view(monkeypatch, [_defn("on"), _defn("off", enabled=False)])
    rows = {t["definition"]: t for t in view["tools"]}
    assert rows["off"]["enabled"] is False
    assert rows["off"]["exposed"] is False
    assert rows["on"]["exposed"] is True
    assert view["summary"]["disabled"] == 1


def test_disabled_is_not_counted_as_a_name_collision(monkeypatch):
    """`shadowed` counts real collisions; folding the two together would lie."""
    view = _view(monkeypatch, [_defn("on"), _defn("off", enabled=False)])
    assert view["summary"]["shadowed"] == 0
    assert all(t["shadowed_by"] is None for t in view["tools"])


def test_a_disabled_definition_does_not_shadow_an_enabled_one(monkeypatch):
    """It never reaches the catalog, so it cannot claim the tool name."""
    view = _view(monkeypatch, [_defn("dup", enabled=False), _defn("dup")])
    rows = [t for t in view["tools"] if t["definition"] == "dup"]
    assert sorted(t["exposed"] for t in rows) == [False, True]
    assert view["summary"]["shadowed"] == 0
    assert view["summary"]["exposed"] == 1
