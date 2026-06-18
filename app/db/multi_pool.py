"""Multi-database connection pool manager.

Loads database configurations from YAML files under the configured
``database_definitions_dir`` directory and manages one ``PoolAdapter``
per database alias.  Supports Oracle, PostgreSQL, and MS SQL Server engines.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

import yaml

from app.core.config import settings
from app.db.pool_adapter import PoolAdapter
from app.db.pool_factory import create_pool_adapter
from app.db.types import DbType

logger = logging.getLogger(__name__)

_ALIAS_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


@dataclass
class DatabaseConfig:
    """Configuration for a single database connection."""

    alias: str
    db_type: DbType = "oracle"
    description: str = ""
    # Oracle-specific
    dsn: str = ""
    # PostgreSQL / MSSQL fields
    host: str = ""
    port: int = 0
    database: str = ""
    # Common
    user: str = ""
    password: str = ""
    pool_min: int = 2
    pool_max: int = 10
    pool_increment: int = 1
    # MSSQL-specific: pin the TDS protocol version (e.g. "7.0" for SQL Server 2005,
    # whose login handshake otherwise hangs under modern FreeTDS auto-negotiation).
    tds_version: str = ""


class DatabaseRegistry:
    """Singleton registry that manages multiple database connection pools."""

    _instance: DatabaseRegistry | None = None

    def __new__(cls) -> DatabaseRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._configs: dict[str, DatabaseConfig] = {}
            cls._instance._pools: dict[str, PoolAdapter] = {}
        return cls._instance

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, directory: str | None = None) -> int:
        """Load database definitions from YAML files.

        Returns the number of databases loaded.
        """
        directory = directory or settings.database_definitions_dir
        dir_path = Path(directory)
        if not dir_path.is_dir():
            logger.warning("Database definitions directory not found: %s", directory)
            return 0

        count = 0
        for fpath in sorted(dir_path.glob("*.yaml")):
            count += self._load_file(fpath)
        for fpath in sorted(dir_path.glob("*.yml")):
            count += self._load_file(fpath)

        # Also support the legacy single-database config via env vars.
        # If no YAML databases were loaded, create a "default" entry from
        # the original single-DB environment variables.
        if count == 0 and settings.oracle_dsn:
            self._configs["default"] = DatabaseConfig(
                alias="default",
                db_type="oracle",
                description="Default database (from environment variables)",
                dsn=settings.oracle_dsn,
                user=settings.oracle_user,
                password=settings.oracle_password,
                pool_min=settings.oracle_pool_min,
                pool_max=settings.oracle_pool_max,
                pool_increment=settings.oracle_pool_increment,
            )
            count = 1
            logger.info("No database YAML found; using legacy env-var config as 'default'")

        logger.info("Loaded %d database definition(s)", count)
        return count

    def _load_file(self, fpath: Path) -> int:
        """Parse a single YAML file (list of database entries)."""
        try:
            with open(fpath, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except Exception:
            logger.exception("Failed to parse %s", fpath)
            return 0

        if not isinstance(raw, list):
            logger.warning("Expected a YAML list in %s, got %s", fpath, type(raw).__name__)
            return 0

        loaded = 0
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            alias = entry.get("alias", "").strip()
            if not alias or not _ALIAS_RE.match(alias):
                logger.warning("Skipping entry with invalid alias: %r", alias)
                continue

            # Allow environment variable override for passwords:
            # DB_{ALIAS}_PASSWORD  (alias uppercased)
            env_key = f"DB_{alias.upper()}_PASSWORD"
            password = os.environ.get(env_key, entry.get("password", ""))

            db_type: DbType = entry.get("db_type", "oracle")

            cfg = DatabaseConfig(
                alias=alias,
                db_type=db_type,
                description=entry.get("description", ""),
                dsn=entry.get("dsn", ""),
                host=entry.get("host", ""),
                port=entry.get("port", 0),
                database=entry.get("database", ""),
                user=entry.get("user", ""),
                password=password,
                pool_min=entry.get("pool_min", 2),
                pool_max=entry.get("pool_max", 10),
                pool_increment=entry.get("pool_increment", 1),
                tds_version=str(entry.get("tds_version", "")),
            )
            self._configs[alias] = cfg
            loaded += 1
            logger.info("Registered database: %s (type=%s)", alias, db_type)

        return loaded

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    def init_all_pools(self) -> None:
        """Create a pool adapter for every registered database.

        A data source that is unreachable (or misconfigured) at startup must
        not prevent the whole application from booting — otherwise one dead
        backend takes down the API and Admin UI for every database. Log the
        failure and continue; the failed alias is retried lazily on first use
        (``get_connection`` re-runs ``init_pool``).
        """
        for alias in self._configs:
            try:
                self.init_pool(alias)
            except Exception:
                logger.exception(
                    "Failed to initialise pool for '%s' at startup; "
                    "it will be retried on first use. Other databases are unaffected.",
                    alias,
                )

    def init_pool(self, alias: str) -> PoolAdapter:
        """Initialise (or return existing) pool for *alias*."""
        if alias in self._pools:
            return self._pools[alias]

        cfg = self._configs.get(alias)
        if cfg is None:
            raise ValueError(f"Unknown database alias: {alias!r}")

        adapter = create_pool_adapter(cfg.db_type)
        adapter.create(cfg)
        self._pools[alias] = adapter
        logger.info(
            "Initialised %s pool for '%s' (min=%d, max=%d)", cfg.db_type, alias, cfg.pool_min, cfg.pool_max
        )
        return adapter

    def close_all_pools(self) -> None:
        """Close every managed pool."""
        for alias, adapter in list(self._pools.items()):
            try:
                adapter.close(force=True)
                logger.info("Closed pool for '%s'", alias)
            except Exception:
                logger.exception("Error closing pool for '%s'", alias)
        self._pools.clear()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    @contextmanager
    def get_connection(self, alias: str) -> Generator[Any, None, None]:
        """Acquire a connection from the pool identified by *alias*."""
        adapter = self._pools.get(alias)
        if adapter is None:
            # Attempt lazy init (useful for Celery workers)
            adapter = self.init_pool(alias)
        with adapter.connection() as conn:
            yield conn

    @contextmanager
    def get_cursor(self, alias: str) -> Generator[Any, None, None]:
        """Acquire a cursor from the pool identified by *alias*."""
        with self.get_connection(alias) as conn:
            cursor = conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    # ------------------------------------------------------------------
    # Engine type helpers
    # ------------------------------------------------------------------

    def get_db_type(self, alias: str) -> DbType:
        """Return the engine type for *alias*."""
        cfg = self._configs.get(alias)
        if cfg is None:
            raise ValueError(f"Unknown database alias: {alias!r}")
        return cfg.db_type

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_databases(self) -> list[dict[str, Any]]:
        """Return a list of configured databases (alias + description + db_type)."""
        result = []
        for alias, cfg in self._configs.items():
            result.append({
                "alias": cfg.alias,
                "db_type": cfg.db_type,
                "description": cfg.description,
                "dsn": cfg.dsn,
                "host": cfg.host,
                "port": cfg.port,
                "database": cfg.database,
                "user": cfg.user,
                "pool_min": cfg.pool_min,
                "pool_max": cfg.pool_max,
                "pool_increment": cfg.pool_increment,
                "tds_version": cfg.tds_version,
                "pool_active": alias in self._pools,
            })
        return result

    def has(self, alias: str) -> bool:
        """Return True if *alias* is a registered database."""
        return alias in self._configs

    def aliases(self) -> list[str]:
        """Return all registered aliases."""
        return list(self._configs.keys())


# Module-level singleton
db_registry = DatabaseRegistry()
