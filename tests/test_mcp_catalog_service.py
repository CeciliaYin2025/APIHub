"""The MCP tools view must report what the MCP server really exposes.

The MCP server derives one tool per (db_alias, SQL definition) and drops any
whose derived name is already taken — in its own process, with no log line and
no trace in the REST API. A definition can therefore be healthy in the Admin UI
and served fine over REST while being invisible to every LLM. These tests pin
the three ways that happens (case/character folding, the ambiguous ``_``
separator between alias and name, and the length cap) and pin the view to
the same naming module the MCP server uses, so the two cannot drift apart.
"""

from app.services.mcp_catalog_service import build_tool_view
from app.services.sql_registry import ParamDef, SqlDefinition


class _FakeDbRegistry:
    def __init__(self, aliases):
        self._aliases = aliases

    def list_databases(self):
        return [{"alias": a} for a in self._aliases]


class _FakeSqlRegistry:
    def __init__(self, by_alias):
        self._by_alias = by_alias

    def list_all(self, db_alias=None, include_disabled=False):
        # Mirrors the real registry: disabled definitions are hidden unless asked for.
        items = self._by_alias.get(db_alias, [])
        return items if include_disabled else [d for d in items if d.enabled]


def _defn(name, alias, description="", params=None):
    return SqlDefinition(
        name=name,
        description=description,
        sql="SELECT 1",
        permission=f"query:{name}",
        db_alias=alias,
        params={p: ParamDef(name=p) for p in (params or [])},
    )


def _view(monkeypatch, aliases, by_alias):
    import app.services.mcp_catalog_service as mod

    monkeypatch.setattr(mod, "db_registry", _FakeDbRegistry(aliases))
    monkeypatch.setattr(mod, "sql_registry", _FakeSqlRegistry(by_alias))
    return build_tool_view()


def _row(view, alias, definition):
    return next(t for t in view["tools"] if t["db_alias"] == alias and t["definition"] == definition)


def test_derives_the_same_names_the_mcp_server_does(monkeypatch):
    from mcp_server.catalog import tool_name

    view = _view(monkeypatch, ["DW"], {"DW": [_defn("eo_detail", "DW"), _defn("employees", "DW")]})
    assert [t["tool_name"] for t in view["tools"]] == [
        tool_name("DW", "eo_detail"),
        tool_name("DW", "employees"),
    ]
    assert all(t["exposed"] for t in view["tools"])
    assert view["summary"] == {"definitions": 2, "exposed": 2, "shadowed": 0, "disabled": 0, "truncated": 0}


def test_view_and_catalog_share_one_naming_implementation():
    # A second copy of these rules would let the view report names the MCP
    # server never serves, which is worse than having no view at all.
    from mcp_server import catalog, naming

    assert catalog.tool_name is naming.tool_name
    assert catalog.sanitize is naming.sanitize


def test_case_and_separator_folding_collide(monkeypatch):
    view = _view(monkeypatch, ["DW"], {"DW": [_defn("eo_detail", "DW"), _defn("EO Detail", "DW")]})
    winner, loser = view["tools"]
    assert winner["tool_name"] == loser["tool_name"] == "query_dw_eo_detail"
    assert winner["exposed"] is True
    assert loser["exposed"] is False
    assert loser["shadowed_by"] == {"db_alias": "DW", "definition": "eo_detail"}
    assert winner["shadows"] == [{"db_alias": "DW", "definition": "EO Detail"}]
    assert view["summary"]["shadowed"] == 1


def test_underscores_in_the_alias_collide_across_databases(monkeypatch):
    # tool_name joins alias and definition with "_" while both may contain "_",
    # so (pg_test, employees) and (pg, test_employees) are indistinguishable.
    view = _view(
        monkeypatch,
        ["pg_test", "pg"],
        {"pg_test": [_defn("employees", "pg_test")], "pg": [_defn("test_employees", "pg")]},
    )
    assert {t["tool_name"] for t in view["tools"]} == {"query_pg_test_employees"}
    dropped = _row(view, "pg", "test_employees")
    assert dropped["exposed"] is False
    assert dropped["shadowed_by"] == {"db_alias": "pg_test", "definition": "employees"}


def test_alias_order_decides_which_definition_survives(monkeypatch):
    # Same two definitions, reversed alias order — the other one now wins.
    view = _view(
        monkeypatch,
        ["pg", "pg_test"],
        {"pg_test": [_defn("employees", "pg_test")], "pg": [_defn("test_employees", "pg")]},
    )
    assert _row(view, "pg", "test_employees")["exposed"] is True
    assert _row(view, "pg_test", "employees")["exposed"] is False


def test_long_names_are_truncated_and_flagged(monkeypatch):
    base = "monthly_shipment_reconciliation_by_customer_and_region_" * 3  # past the 128 cap
    v2, v3 = f"{base}v2", f"{base}v3"
    view = _view(monkeypatch, ["DW"], {"DW": [_defn(v2, "DW"), _defn(v3, "DW")]})

    for row in view["tools"]:
        assert row["truncated"] is True
        assert len(row["tool_name"]) == view["tool_name_max_len"] == 128
    # The distinguishing suffix is exactly what the cap removes.
    assert not view["tools"][0]["tool_name"].endswith(("v2", "v3"))
    assert view["summary"] == {"definitions": 2, "exposed": 1, "shadowed": 1, "disabled": 0, "truncated": 2}


def test_short_names_are_not_flagged_as_truncated(monkeypatch):
    view = _view(monkeypatch, ["DW"], {"DW": [_defn("employees", "DW")]})
    assert view["tools"][0]["truncated"] is False


def test_reports_definition_detail_for_identification(monkeypatch):
    view = _view(
        monkeypatch,
        ["DW"],
        {"DW": [_defn("eo_detail", "DW", description="EO detail rows", params=["frozen_date", "customer"])]},
    )
    row = view["tools"][0]
    assert row["description"] == "EO detail rows"
    assert row["params"] == ["customer", "frozen_date"]
    assert view["db_aliases"] == ["DW"]


def test_alias_with_no_definitions_is_still_listed(monkeypatch):
    view = _view(monkeypatch, ["DW", "HRDC"], {"DW": [_defn("employees", "DW")]})
    assert view["db_aliases"] == ["DW", "HRDC"]
    assert view["summary"]["definitions"] == 1
