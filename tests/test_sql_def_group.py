"""group_name — the free-text category the Admin UI groups the list by.

It replaced ``permission`` as the grouping key, so it has to survive every path a
definition can enter or leave the system through: the ORM column, the registry
dataclass and its two loaders, the CRUD schemas, and the YAML round-trip. It is a
label only — nothing authorizes on it, which is why an empty value is valid.
"""

from pathlib import Path

import yaml

from app.models.sql_definition import SqlDefinitionModel
from app.schemas.sql_definition import SqlDefinitionCreate, SqlDefinitionOut, SqlDefinitionUpdate
from app.services.sql_registry import SqlDefinition, SqlRegistry
from scripts.backfill_sql_def_group import derive_group


def test_model_has_group_name_column():
    row = SqlDefinitionModel(name="q", db_alias="default", sql="SELECT 1", permission="query:q", group_name="HR")
    assert row.group_name == "HR"
    assert "group_name" in SqlDefinitionModel.__table__.columns


def test_column_is_not_the_reserved_word_group():
    # GROUP is reserved in SQL; the column is deliberately group_name so the
    # plain-text ALTER TABLE in scripts/init_db.py needs no quoting.
    assert "group" not in SqlDefinitionModel.__table__.columns


def test_dataclass_defaults_to_empty():
    d = SqlDefinition(name="q", description="", sql="SELECT 1", permission="query:q")
    assert d.group_name == ""


def test_create_schema_defaults_to_empty_and_accepts_a_group():
    assert SqlDefinitionCreate(name="q", sql="SELECT 1").group_name == ""
    assert SqlDefinitionCreate(name="q", sql="SELECT 1", group_name="HR").group_name == "HR"


def test_update_schema_distinguishes_absent_from_cleared():
    # None means "leave it alone"; "" means "clear the group".
    assert SqlDefinitionUpdate(description="x").group_name is None
    assert SqlDefinitionUpdate(group_name="").group_name == ""


def test_out_schema_exposes_group_name():
    out = SqlDefinitionOut(
        id=1, name="q", db_alias="default", group_name="HR", description="", sql="SELECT 1",
        permission="query:q", params={},
    )
    assert out.group_name == "HR"


def test_yaml_loader_reads_group_name(tmp_path: Path):
    (tmp_path / "d.yaml").write_text(
        yaml.dump({"name": "q", "sql": "SELECT 1", "permission": "query:q", "group_name": "HR"}),
        encoding="utf-8",
    )
    reg = SqlRegistry()
    reg._load_from_yaml(str(tmp_path))
    assert reg.get("q").group_name == "HR"


def test_yaml_loader_tolerates_a_definition_written_before_groups_existed(tmp_path: Path):
    (tmp_path / "d.yaml").write_text(
        yaml.dump({"name": "q", "sql": "SELECT 1", "permission": "query:q"}), encoding="utf-8"
    )
    reg = SqlRegistry()
    reg._load_from_yaml(str(tmp_path))
    assert reg.get("q").group_name == ""


def test_backfill_derives_the_group_from_the_name_prefix():
    assert derive_group("HR_SPMFG_Emps") == "HR"
    assert derive_group("eo_max_frozen_date") == "eo"
    # No underscore: the whole name becomes its own group rather than "".
    assert derive_group("ResourceReview") == "ResourceReview"
