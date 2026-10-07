"""Named write definitions: definition shape, request validation, SQL, execution."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db.dialects.oracle_dialect import OracleDialect
from app.db.dialects.postgresql_dialect import PostgresqlDialect
from app.services import named_write_service as svc
from app.services.named_write_service import NamedWriteError, NamedWriteExecutionError

COLUMNS = [
    {"name": "ORGANIZATION_CODE", "type": "string", "required": True},
    {"name": "INVOICE_NO", "type": "string"},
    {"name": "RECEIPT_DATE", "type": "date"},
    {"name": "UNITS_EXPECTED", "type": "number"},
    {"name": "SOURCE_FILE", "type": "string"},
]


def _definition(**over):
    base = dict(
        name="juniper_i698_load",
        db_alias="ebs_ysc",
        table_name="SOM_I698_REPORT",
        mode="replace",
        columns=COLUMNS,
        key_columns=["SOURCE_FILE"],
        max_rows=100,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _row(**over):
    row = {"ORGANIZATION_CODE": "EIAMSFGI", "INVOICE_NO": "INV1", "RECEIPT_DATE": "2026-10-07",
           "UNITS_EXPECTED": 15, "SOURCE_FILE": "f1.csv"}  # fmt: skip
    row.update(over)
    return row


# ── definition shape ─────────────────────────────────────────────────────


def test_shape_accepts_replace_with_declared_key():
    svc.validate_definition_shape("replace", COLUMNS, ["SOURCE_FILE"])


def test_shape_key_is_case_insensitive():
    svc.validate_definition_shape("replace", COLUMNS, ["source_file"])


@pytest.mark.parametrize(
    "mode, columns, keys, message",
    [
        ("replace", COLUMNS, [], "needs at least one key"),
        ("replace", COLUMNS, ["NOPE"], "not a declared column"),
        ("replace", COLUMNS, ["SOURCE_FILE", "source_file"], "duplicate key"),
        ("insert", COLUMNS, ["SOURCE_FILE"], "does not take key"),
        ("upsert", COLUMNS, [], "mode must be"),
        ("insert", [], [], "at least one column"),
        ("insert", [{"name": "A"}, {"name": "a"}], [], "duplicate column"),
        ("insert", [{"name": "A; DROP"}], [], "invalid column name"),
    ],
)
def test_shape_rejects(mode, columns, keys, message):
    with pytest.raises(ValueError, match=message):
        svc.validate_definition_shape(mode, columns, keys)


def test_missing_table_columns_is_case_insensitive():
    assert svc.missing_table_columns(
        COLUMNS, ["organization_code", "INVOICE_NO", "receipt_date", "UNITS_EXPECTED"]
    ) == ["SOURCE_FILE"]


# ── request validation ───────────────────────────────────────────────────


def test_prepare_coerces_types_and_orders_columns_by_definition():
    prepared = svc.prepare(_definition(), {"SOURCE_FILE": "f1.csv"}, [_row(), _row(UNITS_EXPECTED="2.5")])
    assert prepared["columns"] == ["ORGANIZATION_CODE", "INVOICE_NO", "RECEIPT_DATE", "UNITS_EXPECTED", "SOURCE_FILE"]
    assert prepared["rows"][0] == ["EIAMSFGI", "INV1", date(2026, 10, 7), Decimal("15"), "f1.csv"]
    assert prepared["rows"][1][3] == Decimal("2.5")
    assert prepared["key"] == [("SOURCE_FILE", "f1.csv")]


def test_prepare_accepts_slash_dates_and_case_insensitive_names():
    row = {k.lower(): v for k, v in _row(RECEIPT_DATE="2026/04/16").items()}
    prepared = svc.prepare(_definition(), {"source_file": "f1.csv"}, [row])
    assert prepared["rows"][0][2] == date(2026, 4, 16)


def test_prepare_omitted_columns_stay_out_of_the_insert():
    row = _row()
    del row["RECEIPT_DATE"]
    prepared = svc.prepare(_definition(), {"SOURCE_FILE": "f1.csv"}, [row])
    assert "RECEIPT_DATE" not in prepared["columns"]


def test_datetime_type():
    cols = COLUMNS + [{"name": "LOADED_AT", "type": "datetime"}]
    prepared = svc.prepare(
        _definition(columns=cols), {"SOURCE_FILE": "f1.csv"}, [_row(LOADED_AT="2026-10-07T13:05:00")]
    )
    assert prepared["rows"][0][-1] == datetime(2026, 10, 7, 13, 5)


@pytest.mark.parametrize(
    "key, rows, message",
    [
        (None, [_row()], "key is required"),
        ({}, [_row()], "key is required"),
        ({"SOURCE_FILE": ""}, [_row()], "must not be empty"),
        ({"SOURCE_FILE": None}, [_row()], "must not be empty"),
        ({"SOURCE_FILE": "f1.csv", "INVOICE_NO": "x"}, [_row()], "key must have exactly"),
        ({"INVOICE_NO": "x"}, [_row()], "key must have exactly"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(SOURCE_FILE="other.csv")], "must equal the request key"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(EXTRA="x")], "not declared"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(ORGANIZATION_CODE=None)], "ORGANIZATION_CODE is required"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(ORGANIZATION_CODE="  ")], "ORGANIZATION_CODE is required"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(RECEIPT_DATE="16-APR-2026")], "expected a date"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(RECEIPT_DATE="2026-02-30")], "expected a date"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(UNITS_EXPECTED="ten")], "expected a number"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(UNITS_EXPECTED=True)], "expected a number"),
        ({"SOURCE_FILE": "f1.csv"}, [_row(INVOICE_NO=123)], "expected a string"),
    ],
)
def test_prepare_rejects(key, rows, message):
    with pytest.raises(NamedWriteError, match=message):
        svc.prepare(_definition(), key, rows)


def test_key_column_missing_from_row_is_rejected():
    row = _row()
    del row["SOURCE_FILE"]
    with pytest.raises(NamedWriteError, match="SOURCE_FILE is required"):
        svc.prepare(_definition(), {"SOURCE_FILE": "f1.csv"}, [row])


def test_rows_must_share_one_column_set():
    short = _row()
    del short["INVOICE_NO"]
    with pytest.raises(NamedWriteError, match="all rows must match"):
        svc.prepare(_definition(), {"SOURCE_FILE": "f1.csv"}, [_row(), short])


def test_max_rows():
    with pytest.raises(NamedWriteError, match="too many rows"):
        svc.prepare(_definition(max_rows=1), {"SOURCE_FILE": "f1.csv"}, [_row(), _row()])


def test_replace_allows_zero_rows():
    prepared = svc.prepare(_definition(), {"SOURCE_FILE": "f1.csv"}, [])
    assert prepared["rows"] == [] and prepared["key"] == [("SOURCE_FILE", "f1.csv")]


def test_insert_mode_rejects_key_and_empty_rows():
    d = _definition(mode="insert", key_columns=[])
    with pytest.raises(NamedWriteError, match="does not take a key"):
        svc.prepare(d, {"SOURCE_FILE": "f1.csv"}, [_row()])
    with pytest.raises(NamedWriteError, match="must not be empty"):
        svc.prepare(d, None, [])


# ── SQL ──────────────────────────────────────────────────────────────────


def test_statements_oracle_use_positional_binds():
    d = _definition()
    stmts = svc.build_statements(d, svc.prepare(d, {"SOURCE_FILE": "f1.csv"}, [_row()]), OracleDialect())
    assert stmts["delete"] == ("DELETE FROM SOM_I698_REPORT WHERE SOURCE_FILE = :k0", {"k0": "f1.csv"})
    assert stmts["insert"] == (
        "INSERT INTO SOM_I698_REPORT (ORGANIZATION_CODE, INVOICE_NO, RECEIPT_DATE, UNITS_EXPECTED, SOURCE_FILE) "
        "VALUES (:c0, :c1, :c2, :c3, :c4)"
    )
    assert stmts["insert_binds"][0]["c2"] == date(2026, 10, 7)


def test_statements_postgres_placeholders_and_insert_mode_has_no_delete():
    d = _definition(mode="insert", key_columns=[])
    stmts = svc.build_statements(d, svc.prepare(d, None, [_row()]), PostgresqlDialect())
    assert stmts["delete"] is None
    assert "VALUES (%(c0)s, %(c1)s, %(c2)s, %(c3)s, %(c4)s)" in stmts["insert"]


def test_statements_reject_bad_table_name():
    d = _definition(table_name="T; DROP TABLE X")
    with pytest.raises(NamedWriteError, match="invalid table name"):
        svc.build_statements(d, svc.prepare(d, {"SOURCE_FILE": "f1.csv"}, []), OracleDialect())


# ── execution ────────────────────────────────────────────────────────────


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def execute(self, sql, binds):
        self.conn.calls.append(("execute", sql, binds))
        if self.conn.fail_on == "delete":
            raise RuntimeError("ORA-01031: insufficient privileges")
        self.rowcount = self.conn.delete_count

    def executemany(self, sql, binds):
        self.conn.calls.append(("executemany", sql, binds))
        if self.conn.fail_on == "insert":
            raise RuntimeError("ORA-12899: value too large")

    def close(self):
        pass


class _Conn:
    def __init__(self, delete_count=0, fail_on=None):
        self.delete_count = delete_count
        self.fail_on = fail_on
        self.calls = []
        self.committed = self.rolled_back = False

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _patch(monkeypatch, conn):
    @contextmanager
    def get_connection(alias):
        assert alias == "ebs_ysc"
        yield conn

    monkeypatch.setattr(svc, "get_connection", get_connection)
    monkeypatch.setattr(svc, "get_dialect_for", lambda alias: OracleDialect())


def test_execute_deletes_then_inserts_and_commits(monkeypatch):
    conn = _Conn(delete_count=2)
    _patch(monkeypatch, conn)
    result = svc.execute_named_write(_definition(), {"SOURCE_FILE": "f1.csv"}, [_row(), _row(INVOICE_NO="INV2")])
    assert result == {"deleted_rows": 2, "inserted_rows": 2, "committed": True, "dry_run": False}
    assert [c[0] for c in conn.calls] == ["execute", "executemany"]
    assert conn.committed and not conn.rolled_back


def test_dry_run_rolls_back(monkeypatch):
    conn = _Conn(delete_count=1)
    _patch(monkeypatch, conn)
    result = svc.execute_named_write(_definition(), {"SOURCE_FILE": "f1.csv"}, [_row()], dry_run=True)
    assert result["committed"] is False and result["dry_run"] is True and result["deleted_rows"] == 1
    assert conn.rolled_back and not conn.committed


@pytest.mark.parametrize("phase", ["delete", "insert"])
def test_failure_rolls_back_and_reports_phase(monkeypatch, phase):
    conn = _Conn(fail_on=phase)
    _patch(monkeypatch, conn)
    with pytest.raises(NamedWriteExecutionError) as exc:
        svc.execute_named_write(_definition(), {"SOURCE_FILE": "f1.csv"}, [_row()])
    assert exc.value.phase == phase
    assert conn.rolled_back and not conn.committed


def test_invalid_request_never_opens_a_connection(monkeypatch):
    def boom(alias):
        raise AssertionError("must not connect")

    monkeypatch.setattr(svc, "get_connection", boom)
    with pytest.raises(NamedWriteError):
        svc.execute_named_write(_definition(), None, [_row()])
