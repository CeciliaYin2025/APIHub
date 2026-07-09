"""SQL definition registry — loads named queries from PostgreSQL (primary) or YAML (fallback).

Each definition maps to a SQL SELECT statement with metadata such as
allowed parameters, descriptions, and permission requirements.
Definitions are scoped by ``db_alias`` so each database has its own set.

Load priority:
1. PostgreSQL ``api_hub_sql_definitions`` table (primary)
2. YAML files in ``app/sql_definitions/`` (fallback when DB is empty)
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
    db_alias: str = "default"
    params: dict[str, ParamDef] = field(default_factory=dict)
    order_by: list[dict] = field(default_factory=list)


def _key(name: str, db_alias: str) -> str:
    """Registry key combining name + db_alias."""
    return f"{db_alias}::{name}"


class SqlRegistry:
    """In-memory registry of all SQL definitions, keyed by (db_alias, name)."""

    def __init__(self):
        self._definitions: dict[str, SqlDefinition] = {}

    # ── Public API ───────────────────────────────────────────────────────

    def load(self, directory: str | None = None) -> None:
        """Load definitions: try DB first, fall back to YAML if DB is empty."""
        if self._load_from_db():
            return
        self._load_from_yaml(directory)

    def reload(self) -> None:
        """Hot-reload: try DB first, fall back to YAML."""
        self._definitions.clear()
        self.load()

    def reload_from_db(self) -> None:
        """Force reload from DB only (called after CRUD operations)."""
        self._definitions.clear()
        self._load_from_db()

    def get(self, name: str, db_alias: str | None = None) -> SqlDefinition | None:
        """Get a definition by name, optionally scoped to *db_alias*.

        If *db_alias* is provided, only return a match for that database.
        If ``None``, search all (backward compatible).
        """
        if db_alias is not None:
            return self._definitions.get(_key(name, db_alias))
        # Fallback: search all definitions for the name (legacy)
        for k, v in self._definitions.items():
            if v.name == name:
                return v
        return None

    def list_all(self, db_alias: str | None = None) -> list[SqlDefinition]:
        """Return all definitions, optionally filtered by *db_alias*."""
        if db_alias is not None:
            return [d for d in self._definitions.values() if d.db_alias == db_alias]
        return list(self._definitions.values())

    # ── DB loader ────────────────────────────────────────────────────────

    def _load_from_db(self) -> bool:
        """Load definitions from PostgreSQL. Returns True if any were found."""
        try:
            # Lazy import to avoid circular references at module load time
            from app.db.session import SessionLocal
            from app.models.sql_definition import SqlDefinitionModel
        except Exception:
            logger.debug("DB not available for SQL definitions, will use YAML")
            return False

        try:
            db = SessionLocal()
            try:
                rows = db.query(SqlDefinitionModel).all()
                if not rows:
                    return False

                for row in rows:
                    params = {}
                    for pname, pdef in (row.params or {}).items():
                        params[pname] = ParamDef(
                            name=pname,
                            type=pdef.get("type", "string"),
                            required=pdef.get("required", False),
                            description=pdef.get("description", ""),
                        )
                    defn = SqlDefinition(
                        name=row.name,
                        description=row.description or "",
                        sql=row.sql.strip(),
                        permission=row.permission or f"query:{row.name}",
                        db_alias=row.db_alias or "default",
                        params=params,
                        order_by=row.order_by or [],
                    )
                    self._definitions[_key(defn.name, defn.db_alias)] = defn

                logger.info("Loaded %d SQL definitions from database", len(rows))
                return True
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to load SQL definitions from database")
            return False

    # ── YAML loader (fallback) ───────────────────────────────────────────

    def _load_from_yaml(self, directory: str | None = None) -> None:
        """Load all .yaml/.yml files from the SQL definitions directory."""
        base = Path(directory or settings.sql_definitions_dir)
        if not base.exists():
            logger.warning("SQL definitions directory does not exist: %s", base)
            return

        count = 0
        for path in sorted(base.glob("*.y*ml")):
            try:
                self._load_yaml_file(path)
                count += 1
            except Exception:
                logger.exception("Failed to load SQL definition: %s", path)

        logger.info("Loaded %d SQL definitions from YAML (%s)", count, base)

    def _load_yaml_file(self, path: Path) -> None:
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

        db_alias = raw.get("db_alias", "default")
        defn = SqlDefinition(
            name=raw["name"],
            description=raw.get("description", ""),
            sql=raw["sql"].strip(),
            permission=raw.get("permission", f"query:{raw['name']}"),
            db_alias=db_alias,
            params=params,
            order_by=raw.get("order_by") or [],
        )
        self._definitions[_key(defn.name, defn.db_alias)] = defn


# Singleton
sql_registry = SqlRegistry()
