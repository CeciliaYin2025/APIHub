"""Write definition endpoints: CRUD rules, permission, audit, error mapping."""

import pytest
from fastapi import HTTPException

from app.api.v1 import write_definitions as api
from app.models.user import Permission, Role, User
from app.models.write_definition import WriteDefinitionModel
from app.schemas.write_definition import NamedWriteRequest, WriteDefinitionCreate, WriteDefinitionUpdate
from app.services.named_write_service import NamedWriteError, NamedWriteExecutionError

COLUMNS = [
    {"name": "ORGANIZATION_CODE", "type": "string", "required": True},
    {"name": "SOURCE_FILE", "type": "string"},
]


class _Query:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result

    def all(self):
        return [self._result] if self._result else []


class _DB:
    def __init__(self, existing=None):
        self._existing = existing
        self.added, self.deleted = [], []
        self.commits = 0

    def query(self, _m):
        return _Query(self._existing)

    def add(self, o):
        self.added.append(o)

    def delete(self, o):
        self.deleted.append(o)

    def commit(self):
        self.commits += 1

    def refresh(self, o):
        pass


class _Req:
    def __init__(self):
        self.state = type("S", (), {"principal": None})()


def _admin():
    return User(username="admin", is_superuser=True, is_active=True)


def _user(*perms):
    role = Role(name="r", permissions=[Permission(resource=r, action=a) for r, a in perms])
    return User(username="svc", is_superuser=False, is_active=True, roles=[role])


def _row(**over):
    base = dict(
        id=1,
        name="juniper_i698_load",
        db_alias="ebs_ysc",
        group_name="juniper",
        description="",
        table_name="SOM_I698_REPORT",
        mode="replace",
        columns=COLUMNS,
        key_columns=["SOURCE_FILE"],
        max_rows=100,
        permission="write:juniper_i698_load",
        enabled=True,
    )
    base.update(over)
    return WriteDefinitionModel(**base)


def _create_body(**over):
    base = dict(
        name="juniper_i698_load",
        table_name="SOM_I698_REPORT",
        mode="replace",
        columns=COLUMNS,
        key_columns=["SOURCE_FILE"],
    )
    base.update(over)
    return WriteDefinitionCreate(**base)


# ── create / update / delete ─────────────────────────────────────────────


def test_create_defaults_permission_and_checks_table(monkeypatch):
    checked = []
    monkeypatch.setattr(api, "check_against_table", lambda row: checked.append(row.table_name) or [])
    db = _DB()
    out = api.create_definition(_Req(), _create_body(), db="ebs_ysc", current_user=_admin(), pgdb=db)
    assert out["permission"] == "write:juniper_i698_load"
    assert out["db_alias"] == "ebs_ysc"
    assert checked == ["SOM_I698_REPORT"]
    assert db.added[0].columns[0] == {
        "name": "ORGANIZATION_CODE",
        "type": "string",
        "required": True,
        "description": "",
    }


def test_create_reports_columns_missing_from_table(monkeypatch):
    monkeypatch.setattr(api, "check_against_table", lambda row: ["SOURCE_FILE"])
    db = _DB()
    with pytest.raises(HTTPException) as exc:
        api.create_definition(_Req(), _create_body(), db="ebs_ysc", current_user=_admin(), pgdb=db)
    assert exc.value.status_code == 400 and "SOURCE_FILE" in exc.value.detail
    assert db.added == []


def test_create_table_not_visible_is_400(monkeypatch):
    def raise_(row):
        raise ValueError("Table or view 'SOM_I698_REPORT' not found or not accessible")

    monkeypatch.setattr(api, "check_against_table", raise_)
    with pytest.raises(HTTPException) as exc:
        api.create_definition(_Req(), _create_body(), db="default", current_user=_admin(), pgdb=_DB())
    assert exc.value.status_code == 400


def test_create_skip_table_check(monkeypatch):
    monkeypatch.setattr(api, "check_against_table", lambda row: pytest.fail("must not check"))
    db = _DB()
    api.create_definition(_Req(), _create_body(), skip_table_check=True, db="ebs_ysc", current_user=_admin(), pgdb=db)
    assert len(db.added) == 1


def test_create_duplicate_is_409():
    with pytest.raises(HTTPException) as exc:
        api.create_definition(_Req(), _create_body(), db="ebs_ysc", current_user=_admin(), pgdb=_DB(existing=_row()))
    assert exc.value.status_code == 409


def test_crud_requires_superuser():
    with pytest.raises(HTTPException) as exc:
        api.list_definitions(db="ebs_ysc", current_user=_user(("write:*", "write")), pgdb=_DB())
    assert exc.value.status_code == 403


def test_create_schema_rejects_replace_without_key():
    with pytest.raises(ValueError, match="needs at least one key"):
        _create_body(key_columns=[])


def test_update_revalidates_merged_shape(monkeypatch):
    monkeypatch.setattr(api, "check_against_table", lambda row: [])
    row = _row()
    with pytest.raises(HTTPException) as exc:
        api.update_definition(
            _Req(), "juniper_i698_load", WriteDefinitionUpdate(key_columns=[]), db="ebs_ysc",
            current_user=_admin(), pgdb=_DB(existing=row),
        )  # fmt: skip
    assert exc.value.status_code == 422
    assert row.key_columns == ["SOURCE_FILE"]


def test_update_metadata_only_skips_table_check(monkeypatch):
    monkeypatch.setattr(api, "check_against_table", lambda row: pytest.fail("must not check"))
    row = _row()
    api.update_definition(
        _Req(), "juniper_i698_load", WriteDefinitionUpdate(description="d", enabled=False), db="ebs_ysc",
        current_user=_admin(), pgdb=_DB(existing=row),
    )  # fmt: skip
    assert row.description == "d" and row.enabled is False


def test_update_columns_rechecks_table(monkeypatch):
    monkeypatch.setattr(api, "check_against_table", lambda row: ["NEW_COL"])
    body = WriteDefinitionUpdate(columns=COLUMNS + [{"name": "NEW_COL"}])
    with pytest.raises(HTTPException) as exc:
        api.update_definition(
            _Req(), "juniper_i698_load", body, db="ebs_ysc", current_user=_admin(), pgdb=_DB(existing=_row())
        )
    assert exc.value.status_code == 400


def test_delete_and_not_found():
    db = _DB(existing=_row())
    api.delete_definition(_Req(), "juniper_i698_load", db="ebs_ysc", current_user=_admin(), pgdb=db)
    assert len(db.deleted) == 1
    with pytest.raises(HTTPException) as exc:
        api.get_definition("nope", db="ebs_ysc", current_user=_admin(), pgdb=_DB())
    assert exc.value.status_code == 404


# ── execute ──────────────────────────────────────────────────────────────


def _payload(**over):
    base = dict(
        name="juniper_i698_load",
        key={"SOURCE_FILE": "f.csv"},
        rows=[{"ORGANIZATION_CODE": "X", "SOURCE_FILE": "f.csv"}],
    )
    base.update(over)
    return NamedWriteRequest(**base)


def test_execute_success_audits_separate_counts(monkeypatch):
    result = {"deleted_rows": 2, "inserted_rows": 1, "committed": True, "dry_run": False}
    monkeypatch.setattr(api, "execute_named_write", lambda row, key, rows, dry_run: result)
    req = _Req()
    resp = api.execute(req, _payload(), db="ebs_ysc", current_user=_user(("write:juniper_i698_load", "write")),
                       pgdb=_DB(existing=_row()))  # fmt: skip
    assert resp.deleted_rows == 2 and resp.inserted_rows == 1 and resp.mode == "replace"
    assert req.state.audit_action_type == "write"
    assert req.state.audit_action == "juniper_i698_load"
    assert req.state.audit_params["key"] == {"SOURCE_FILE": "f.csv"}
    assert req.state.audit_params["result"] == result
    assert req.state.audit_result_rows == 3


def test_execute_table_permission_is_not_enough(monkeypatch):
    monkeypatch.setattr(api, "execute_named_write", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(HTTPException) as exc:
        api.execute(
            _Req(), _payload(), db="ebs_ysc", current_user=_user(("table:*", "write")), pgdb=_DB(existing=_row())
        )
    assert exc.value.status_code == 403


def test_execute_wildcard_write_permission(monkeypatch):
    monkeypatch.setattr(api, "execute_named_write", lambda *a, **k: {
        "deleted_rows": 0, "inserted_rows": 1, "committed": True, "dry_run": False})  # fmt: skip
    resp = api.execute(_Req(), _payload(), db="ebs_ysc", current_user=_user(("write:*", "write")),
                       pgdb=_DB(existing=_row()))  # fmt: skip
    assert resp.inserted_rows == 1


def test_execute_unknown_and_disabled():
    user = _user(("write:*", "write"))
    with pytest.raises(HTTPException) as exc:
        api.execute(_Req(), _payload(), db="default", current_user=user, pgdb=_DB())
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        api.execute(_Req(), _payload(), db="ebs_ysc", current_user=user, pgdb=_DB(existing=_row(enabled=False)))
    assert exc.value.status_code == 409


def test_execute_validation_error_is_400_and_still_audited(monkeypatch):
    def bad(*a, **k):
        raise NamedWriteError("row 0: SOURCE_FILE must equal the request key 'f.csv'")

    monkeypatch.setattr(api, "execute_named_write", bad)
    req = _Req()
    with pytest.raises(HTTPException) as exc:
        api.execute(req, _payload(), db="ebs_ysc", current_user=_admin(), pgdb=_DB(existing=_row()))
    assert exc.value.status_code == 400 and "request key" in exc.value.detail
    assert req.state.audit_action == "juniper_i698_load"


def test_execute_database_error_reports_phase(monkeypatch):
    def bad(*a, **k):
        raise NamedWriteExecutionError("insert", "ORA-12899: value too large")

    monkeypatch.setattr(api, "execute_named_write", bad)
    with pytest.raises(HTTPException) as exc:
        api.execute(_Req(), _payload(), db="ebs_ysc", current_user=_admin(), pgdb=_DB(existing=_row()))
    assert exc.value.status_code == 400
    assert exc.value.detail["phase"] == "insert" and exc.value.detail["committed"] is False
