"""GET /databases must not hand connection details to every authenticated caller.

The endpoint only requires a valid login, so any API key — including a
service account with no permissions at all, and every MCP caller via
list_databases — could read each data source's host, port, service name and
database username, production ERP included. Callers need the alias to route a
request; they do not need the credentials-adjacent fields to do it.
"""

from app.api.v1.databases import list_databases
from app.models.user import User

PUBLIC_FIELDS = {"alias", "db_type", "description", "pool_active"}
SENSITIVE_FIELDS = {"dsn", "host", "port", "database", "user", "pool_min", "pool_max", "pool_increment", "tds_version"}


class _FakeRegistry:
    def list_databases(self):
        return [
            {
                "alias": "erp_prod",
                "db_type": "oracle",
                "description": "Oracle ERP Prod database",
                "dsn": "172.31.3.210:1521/PROD",
                "host": "",
                "port": 0,
                "database": "",
                "user": "YPR",
                "pool_min": 2,
                "pool_max": 10,
                "pool_increment": 1,
                "tds_version": "",
                "pool_active": True,
            },
            {
                "alias": "DW",
                "db_type": "mssql",
                "description": "Data warehouse",
                "dsn": "",
                "host": "172.31.3.31",
                "port": 1433,
                "database": "S_DW",
                "user": "dwReadonly",
                "pool_min": 2,
                "pool_max": 10,
                "pool_increment": 1,
                "tds_version": "7.4",
                "pool_active": True,
            },
        ]


def _user(superuser: bool) -> User:
    return User(username="u", is_active=True, is_superuser=superuser, is_service_account=False)


def _call(monkeypatch, superuser: bool):
    import app.api.v1.databases as mod

    monkeypatch.setattr(mod, "db_registry", _FakeRegistry())
    return list_databases(current_user=_user(superuser))


def test_non_superuser_gets_only_routing_fields(monkeypatch):
    rows = _call(monkeypatch, superuser=False)
    assert len(rows) == 2
    for row in rows:
        assert set(row) == PUBLIC_FIELDS


def test_non_superuser_never_sees_any_connection_detail(monkeypatch):
    blob = repr(_call(monkeypatch, superuser=False))
    for leaked in ("172.31.3.210", "172.31.3.31", "PROD", "S_DW", "YPR", "dwReadonly", "1433"):
        assert leaked not in blob, f"{leaked!r} leaked to a non-superuser"


def test_non_superuser_keeps_what_routing_needs(monkeypatch):
    rows = _call(monkeypatch, superuser=False)
    assert [r["alias"] for r in rows] == ["erp_prod", "DW"]
    assert [r["db_type"] for r in rows] == ["oracle", "mssql"]
    assert rows[1]["description"] == "Data warehouse"
    assert rows[0]["pool_active"] is True


def test_superuser_still_sees_everything(monkeypatch):
    rows = _call(monkeypatch, superuser=True)
    for row in rows:
        assert SENSITIVE_FIELDS <= set(row)
    assert rows[1]["host"] == "172.31.3.31"
    assert rows[1]["user"] == "dwReadonly"


def test_redaction_does_not_mutate_the_registry(monkeypatch):
    # The registry hands back its own dicts; redacting in place would strip the
    # fields permanently for the superuser view too.
    import app.api.v1.databases as mod

    reg = _FakeRegistry()
    monkeypatch.setattr(mod, "db_registry", reg)
    list_databases(current_user=_user(False))
    assert "host" in reg.list_databases()[1]
    assert list_databases(current_user=_user(True))[1]["host"] == "172.31.3.31"
