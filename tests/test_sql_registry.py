"""Tests for the SQL definition registry."""

import tempfile
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
