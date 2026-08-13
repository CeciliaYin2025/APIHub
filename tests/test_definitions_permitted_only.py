"""GET /{db}/queries/definitions?permitted_only=true — discovery-side RBAC.

Execution has always been gated by ``PermissionChecker`` on ``/execute``;
*listing* was not, so any authenticated caller could read back the name,
description and parameters of every definition on the alias. That is tolerable
for a management UI and not tolerable for the MCP tool catalog, which pushes
the whole list into an LLM's context.

The flag is opt-in, so the default (unfiltered) behaviour the Admin UI's
SQL Definitions page depends on is asserted here too.
"""

import app.api.v1.queries as queries_api
from app.models.user import Permission, Role, User
from app.services.sql_registry import SqlDefinition


def _defs():
    return [
        SqlDefinition(name="emps", description="", sql="", permission="query:emps", db_alias="dw"),
        SqlDefinition(name="salaries", description="", sql="", permission="query:salaries", db_alias="dw"),
        SqlDefinition(name="rd_tc_all", description="", sql="", permission="query:rd_tc_all", db_alias="dw"),
    ]


def _user(*perms: str, is_superuser: bool = False) -> User:
    role = Role(name="r", permissions=[Permission(resource=p, action="read") for p in perms])
    return User(username="u", is_superuser=is_superuser, is_active=True, roles=[role])


def _call(monkeypatch, user, **kwargs):
    class _Registry:
        def list_all(self, db_alias=None):
            return _defs()

    monkeypatch.setattr(queries_api, "sql_registry", _Registry())
    rows = queries_api.list_definitions(db="dw", current_user=user, **kwargs)
    return [r["name"] for r in rows]


def test_defaults_to_the_full_list(monkeypatch):
    # The Admin UI management view relies on this: a definition must not vanish
    # from the page that maintains it.
    assert _call(monkeypatch, _user("query:emps")) == ["emps", "salaries", "rd_tc_all"]


def test_permitted_only_drops_definitions_the_caller_cannot_run(monkeypatch):
    assert _call(monkeypatch, _user("query:emps"), permitted_only=True) == ["emps"]


def test_permitted_only_honours_wildcards(monkeypatch):
    assert _call(monkeypatch, _user("query:*"), permitted_only=True) == ["emps", "salaries", "rd_tc_all"]
    assert _call(monkeypatch, _user("query:rd_tc*"), permitted_only=True) == ["rd_tc_all"]


def test_permitted_only_can_return_nothing(monkeypatch):
    assert _call(monkeypatch, _user(), permitted_only=True) == []


def test_permitted_only_is_a_noop_for_superusers(monkeypatch):
    user = _user(is_superuser=True)
    assert _call(monkeypatch, user, permitted_only=True) == ["emps", "salaries", "rd_tc_all"]


def test_filter_matches_on_permission_not_on_definition_name(monkeypatch):
    """`permission` is free-form and defaults to `query:<name>` but may be shared.

    Filtering on the definition name instead would deny a caller holding the
    shared permission, so pin the field the check actually reads.
    """

    class _Registry:
        def list_all(self, db_alias=None):
            return [
                SqlDefinition(name="a", description="", sql="", permission="query:shared", db_alias="dw"),
                SqlDefinition(name="b", description="", sql="", permission="query:shared", db_alias="dw"),
            ]

    monkeypatch.setattr(queries_api, "sql_registry", _Registry())
    rows = queries_api.list_definitions(db="dw", permitted_only=True, current_user=_user("query:shared"))
    assert [r["name"] for r in rows] == ["a", "b"]
