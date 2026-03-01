"""SQL definition registry — loads YAML files that define named queries.

Each YAML file represents a named query that maps to a SQL SELECT statement,
with metadata such as allowed parameters and descriptions.

Example YAML (app/sql_definitions/employees.yaml):
    name: employees
    description: "All active employees"
    sql: |
      SELECT emp_id, emp_name, department, hire_date
      FROM employees
      WHERE status = 'ACTIVE'
      AND (:dept IS NULL OR department = :dept)
    params:
      dept:
        type: string
        required: false
        description: "Filter by department"
    permission: "query:employees"
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ParamDef:
    name: str
    type: str = "string"  # string, number, date
    required: bool = False
    description: str = ""


@dataclass
class SqlDefinition:
    name: str
    description: str
    sql: str
    permission: str  # e.g. "query:employees"
    params: dict[str, ParamDef] = field(default_factory=dict)


class SqlRegistry:
    """In-memory registry of all SQL definitions loaded from YAML files."""

    def __init__(self):
        self._definitions: dict[str, SqlDefinition] = {}

    def load(self, directory: str | None = None) -> None:
        """Load all .yaml/.yml files from the SQL definitions directory."""
        base = Path(directory or settings.sql_definitions_dir)
        if not base.exists():
            logger.warning("SQL definitions directory does not exist: %s", base)
            return

        count = 0
        for path in sorted(base.glob("*.y*ml")):
            try:
                self._load_file(path)
                count += 1
            except Exception:
                logger.exception("Failed to load SQL definition: %s", path)

        logger.info("Loaded %d SQL definitions from %s", count, base)

    def _load_file(self, path: Path) -> None:
        with open(path) as f:
            raw = yaml.safe_load(f)

        params = {}
        for pname, pdef in (raw.get("params") or {}).items():
            params[pname] = ParamDef(
                name=pname,
                type=pdef.get("type", "string"),
                required=pdef.get("required", False),
                description=pdef.get("description", ""),
            )

        defn = SqlDefinition(
            name=raw["name"],
            description=raw.get("description", ""),
            sql=raw["sql"].strip(),
            permission=raw.get("permission", f"query:{raw['name']}"),
            params=params,
        )
        self._definitions[defn.name] = defn

    def get(self, name: str) -> SqlDefinition | None:
        return self._definitions.get(name)

    def list_all(self) -> list[SqlDefinition]:
        return list(self._definitions.values())

    def reload(self) -> None:
        """Hot-reload definitions without restarting the server."""
        self._definitions.clear()
        self.load()


# Singleton
sql_registry = SqlRegistry()
