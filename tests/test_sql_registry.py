"""Tests for the SQL definition registry."""

from pathlib import Path

import pytest
import yaml

from app.services.sql_registry import SqlRegistry


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """Create a temp directory with sample YAML definitions."""
    defn = {
        "name": "test_query",
        "description": "A test query",
        "sql": "SELECT id, name FROM test_table WHERE (:status IS NULL OR status = :status)",
        "params": {
            "status": {
                "type": "string",
                "required": False,
                "description": "Filter by status",
            }
        },
        "permission": "query:test_query",
    }
    path = tmp_path / "test_query.yaml"
    path.write_text(yaml.dump(defn))
    return tmp_path


def test_load_definitions(sample_dir: Path):
    registry = SqlRegistry()
    registry.load(str(sample_dir))

    assert len(registry.list_all()) == 1
    defn = registry.get("test_query")
    assert defn is not None
    assert defn.name == "test_query"
    assert "test_table" in defn.sql
    assert "status" in defn.params
    assert defn.params["status"].required is False
    assert defn.permission == "query:test_query"


def test_missing_definition(sample_dir: Path):
    registry = SqlRegistry()
    registry.load(str(sample_dir))

    assert registry.get("nonexistent") is None


def test_reload(sample_dir: Path):
    registry = SqlRegistry()
    registry.load(str(sample_dir))
    assert len(registry.list_all()) == 1

    # Add another definition
    defn2 = {
        "name": "second_query",
        "description": "Another query",
        "sql": "SELECT 1 FROM DUAL",
        "permission": "query:second_query",
    }
    (sample_dir / "second.yaml").write_text(yaml.dump(defn2))

    # Reload picks up the new file
    registry._definitions.clear()
    registry.load(str(sample_dir))
    assert len(registry.list_all()) == 2


def test_empty_directory(tmp_path: Path):
    registry = SqlRegistry()
    registry.load(str(tmp_path))
    assert len(registry.list_all()) == 0


def test_load_yaml_file_reads_utf8_regardless_of_locale(tmp_path: Path):
    """Definition files are UTF-8; loading must not depend on the platform locale.

    Regression: _load_yaml_file used a bare open(), which decodes with the locale
    encoding (cp950 on the Windows dev machines) and raises UnicodeDecodeError on any
    non-ASCII description. _load_from_yaml swallows that into a logged exception, so
    the definition silently went missing instead of failing loudly.

    _load_yaml_file is called directly because load() prefers the DB and would ignore
    the directory argument entirely.
    """
    defn = {
        "name": "zh_query",
        "description": "員工基本資料查詢",
        "sql": "SELECT 1 FROM DUAL",
    }
    path = tmp_path / "zh_query.yaml"
    # allow_unicode=True keeps real UTF-8 bytes on disk; the default escapes them to
    # ASCII \uXXXX sequences, which would not exercise the decode path at all.
    path.write_text(yaml.dump(defn, allow_unicode=True), encoding="utf-8")

    registry = SqlRegistry()
    registry._load_yaml_file(path)

    assert registry.get("zh_query").description == "員工基本資料查詢"
