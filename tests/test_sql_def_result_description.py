"""result_description — the free-text summary of what a query gives back.

It exists for one consumer: the MCP tool description an LLM reads before
choosing a tool (``mcp_server/catalog.py::build_description``). Without it the
only way to learn a query's shape is to run it, which costs a round trip and
fills the model's context with rows that may be the wrong thing entirely.

Like ``group_name`` it is a label — nothing validates it against the actual
result set, and an empty value is valid — so what matters is that it survives
every path a definition travels: the ORM column, the registry dataclass and
both loaders, the CRUD schemas, and the YAML round-trip.
"""

from pathlib import Path

import yaml

from app.models.sql_definition import SqlDefinitionModel
from app.schemas.sql_definition import SqlDefinitionCreate, SqlDefinitionOut, SqlDefinitionUpdate
from app.services.sql_registry import SqlDefinition, SqlRegistry

COLUMNS = "EMP_NO, NAME, DEPT, HIRE_DATE"


def test_model_has_the_column():
    row = SqlDefinitionModel(
        name="q", db_alias="default", sql="SELECT 1", permission="query:q", result_description=COLUMNS
    )
    assert row.result_description == COLUMNS
    assert "result_description" in SqlDefinitionModel.__table__.columns


def test_column_is_text_not_a_bounded_string():
    # A column list for a wide view runs past any varchar cap worth picking, and
    # a silently truncated one would mislead the model rather than merely fail.
    assert SqlDefinitionModel.__table__.columns["result_description"].type.python_type is str
    assert getattr(SqlDefinitionModel.__table__.columns["result_description"].type, "length", None) is None


def test_dataclass_defaults_to_empty():
    assert SqlDefinition(name="q", description="", sql="SELECT 1", permission="query:q").result_description == ""


def test_create_schema_defaults_to_empty_and_accepts_a_value():
    assert SqlDefinitionCreate(name="q", sql="SELECT 1").result_description == ""
    assert SqlDefinitionCreate(name="q", sql="SELECT 1", result_description=COLUMNS).result_description == COLUMNS


def test_update_schema_distinguishes_absent_from_cleared():
    # None means "leave it alone"; "" means "clear it".
    assert SqlDefinitionUpdate(description="x").result_description is None
    assert SqlDefinitionUpdate(result_description="").result_description == ""


def test_out_schema_exposes_it():
    out = SqlDefinitionOut(
        id=1,
        name="q",
        db_alias="default",
        description="",
        result_description=COLUMNS,
        sql="SELECT 1",
        permission="query:q",
        params={},
    )
    assert out.result_description == COLUMNS


def test_yaml_loader_reads_it(tmp_path: Path):
    (tmp_path / "d.yaml").write_text(
        yaml.dump({"name": "q", "sql": "SELECT 1", "permission": "query:q", "result_description": COLUMNS}),
        encoding="utf-8",
    )
    reg = SqlRegistry()
    reg._load_from_yaml(str(tmp_path))
    assert reg.get("q").result_description == COLUMNS


def test_yaml_loader_tolerates_a_definition_written_before_the_field_existed(tmp_path: Path):
    (tmp_path / "d.yaml").write_text(
        yaml.dump({"name": "q", "sql": "SELECT 1", "permission": "query:q"}), encoding="utf-8"
    )
    reg = SqlRegistry()
    reg._load_from_yaml(str(tmp_path))
    assert reg.get("q").result_description == ""


def test_listing_carries_it_to_the_mcp_catalog(monkeypatch):
    """The MCP server only ever sees definitions through GET .../definitions.

    If the listing drops the field the whole feature is inert while every
    storage-layer test above still passes, so pin the endpoint's payload.
    """
    import app.api.v1.queries as queries_api
    from app.models.user import User

    class _Registry:
        def list_all(self, db_alias=None, include_disabled=False):
            # Mirrors the real registry: disabled ones are hidden unless asked for.
            return [
                SqlDefinition(
                    name="q",
                    description="d",
                    sql="SELECT 1",
                    permission="query:q",
                    db_alias="dw",
                    group_name="HR",
                    result_description=COLUMNS,
                )
            ]

    monkeypatch.setattr(queries_api, "sql_registry", _Registry())
    user = User(username="u", is_superuser=True, is_active=True, roles=[])
    row = queries_api.list_definitions(db="dw", current_user=user)[0]
    assert row["result_description"] == COLUMNS
    assert row["group_name"] == "HR"
