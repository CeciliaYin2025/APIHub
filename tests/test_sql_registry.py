"""Tests for the SQL definition registry."""

from pathlib import Path

import pytest
import yaml

from app.services.sql_registry import SqlDefinition, SqlRegistry, _key


@pytest.fixture
def yaml_only(monkeypatch):
    """Force the YAML fallback path.

    ``load()`` tries PostgreSQL first and returns early the moment it finds
    anything, ignoring its ``directory`` argument entirely. Without this
    fixture, any test that passes a temp directory silently asserts against
    whatever the developer's platform store happens to hold — which is exactly
    what used to make the tests below pass or fail depending on local DB state.
    """
    monkeypatch.setattr(SqlRegistry, "_load_from_db", lambda self: False)


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


def test_load_definitions(yaml_only, sample_dir: Path):
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


def test_missing_definition(yaml_only, sample_dir: Path):
    registry = SqlRegistry()
    registry.load(str(sample_dir))

    assert registry.get("nonexistent") is None


def test_reload(yaml_only, sample_dir: Path):
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


def test_empty_directory(yaml_only, tmp_path: Path):
    registry = SqlRegistry()
    registry.load(str(tmp_path))
    assert len(registry.list_all()) == 0


def test_load_prefers_db_and_never_reads_yaml(sample_dir: Path, monkeypatch):
    """DB wins outright: a non-empty platform store means YAML is not consulted.

    This is the contract whose introduction silently broke the tests above —
    they passed a directory and assumed it was used. Asserting it here means a
    future change to the load-priority order fails loudly instead of turning
    those tests environment-dependent again.
    """
    from_db = SqlDefinition(name="from_db", description="", sql="SELECT 1", permission="query:from_db")

    def _fake_load_from_db(self) -> bool:
        self._definitions[_key(from_db.name, from_db.db_alias)] = from_db
        return True

    yaml_calls = []
    monkeypatch.setattr(SqlRegistry, "_load_from_db", _fake_load_from_db)
    monkeypatch.setattr(SqlRegistry, "_load_from_yaml", lambda self, directory=None: yaml_calls.append(directory))

    registry = SqlRegistry()
    registry.load(str(sample_dir))

    assert [d.name for d in registry.list_all()] == ["from_db"]
    assert yaml_calls == [], "load() must not fall back to YAML when the DB returned definitions"


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
