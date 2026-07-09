"""create/update definition persists order_by; _row_to_out returns it."""

from app.api.v1 import queries
from app.models.sql_definition import SqlDefinitionModel
from app.models.user import User
from app.schemas.sql_definition import SqlDefinitionCreate, SqlDefinitionUpdate


class _Query:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _DB:
    def __init__(self, existing=None):
        self._existing = existing
        self.added = []

    def query(self, _m):
        return _Query(self._existing)

    def add(self, o):
        self.added.append(o)

    def commit(self):
        pass

    def refresh(self, o):
        pass


def _admin():
    return User(username="admin", is_superuser=True, is_active=True)


def test_row_to_out_includes_order_by():
    row = SqlDefinitionModel(
        name="q",
        db_alias="default",
        sql="SELECT 1",
        permission="query:q",
        order_by=[{"column": "a", "direction": "DESC"}],
    )
    out = queries._row_to_out(row)
    assert out["order_by"] == [{"column": "a", "direction": "DESC"}]


def test_create_persists_order_by(monkeypatch):
    monkeypatch.setattr(queries.sql_registry, "reload_from_db", lambda: None)
    db = _DB(existing=None)
    body = SqlDefinitionCreate(name="q", sql="SELECT 1", order_by=[{"column": "a", "direction": "DESC"}])
    queries.create_definition(body, db="default", current_user=_admin(), pgdb=db)
    assert db.added[0].order_by == [{"column": "a", "direction": "DESC"}]


def test_update_replaces_order_by(monkeypatch):
    monkeypatch.setattr(queries.sql_registry, "reload_from_db", lambda: None)
    row = SqlDefinitionModel(name="q", db_alias="default", sql="SELECT 1", permission="query:q", order_by=[])
    body = SqlDefinitionUpdate(order_by=[{"column": "b", "direction": "ASC"}])
    queries.update_definition("q", body, db="default", current_user=_admin(), pgdb=_DB(existing=row))
    assert row.order_by == [{"column": "b", "direction": "ASC"}]
