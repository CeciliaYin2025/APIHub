"""Multi-database connection pool manager.

Loads database configurations from YAML files under the configured
``database_definitions_dir`` directory and manages one ``cx_Oracle.SessionPool``
per database alias.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

import oracledb as cx_Oracle
import yaml

from app.core.config import settings

logger = logging.getLogger(__name__)

# Enable thick mode for Oracle 11g compatibility
_thick_mode_initialized = False


def _ensure_thick_mode() -> None:
    """Initialise Oracle Client (thick mode) once, if configured."""
    global _thick_mode_initialized
    if _thick_mode_initialized:
        return
    lib_dir = settings.oracle_client_dir or None
    try:
        cx_Oracle.init_oracle_client(lib_dir=lib_dir)
        logger.info("Oracle thick mode enabled (lib_dir=%s)", lib_dir)
    except cx_Oracle.ProgrammingError:
        # Already initialised (e.g. in Celery worker)
        pass
    _thick_mode_initialized = True

_ALIAS_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


@dataclass
class DatabaseConfig:
    """Configuration for a single Oracle database connection."""

    alias: str
    description: str = ""
    dsn: str = ""
    user: str = ""
    password: str = ""
    pool_min: int = 2
    pool_max: int = 10
    pool_increment: int = 1


class DatabaseRegistry:
    """Singleton registry that manages multiple Oracle connection pools."""

    _instance: DatabaseRegistry | None = None

    def __new__(cls) -> DatabaseRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._configs: dict[str, DatabaseConfig] = {}
            cls._instance._pools: dict[str, cx_Oracle.SessionPool] = {}
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

            cfg = DatabaseConfig(
                alias=alias,
                description=entry.get("description", ""),
                dsn=entry.get("dsn", ""),
                user=entry.get("user", ""),
                password=password,
                pool_min=entry.get("pool_min", 2),
                pool_max=entry.get("pool_max", 10),
                pool_increment=entry.get("pool_increment", 1),
            )
            self._configs[alias] = cfg
            loaded += 1
            logger.info("Registered database: %s (%s)", alias, cfg.dsn)

        return loaded

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    def init_all_pools(self) -> None:
        """Create a ``cx_Oracle.SessionPool`` for every registered database."""
        _ensure_thick_mode()
        for alias, cfg in self._configs.items():
            self.init_pool(alias)

    def init_pool(self, alias: str) -> cx_Oracle.SessionPool:
        """Initialise (or return existing) pool for *alias*."""
        _ensure_thick_mode()
        if alias in self._pools:
            return self._pools[alias]

        cfg = self._configs.get(alias)
        if cfg is None:
            raise ValueError(f"Unknown database alias: {alias!r}")

        pool = cx_Oracle.SessionPool(
            user=cfg.user,
            password=cfg.password,
            dsn=cfg.dsn,
            min=cfg.pool_min,
            max=cfg.pool_max,
            increment=cfg.pool_increment,
            encoding="UTF-8",
            threaded=True,
            getmode=cx_Oracle.SPOOL_ATTRVAL_WAIT,
        )
        self._pools[alias] = pool
        logger.info("Initialised pool for '%s' (min=%d, max=%d)", alias, cfg.pool_min, cfg.pool_max)
        return pool

    def close_all_pools(self) -> None:
        """Close every managed pool."""
        for alias, pool in list(self._pools.items()):
            try:
                pool.close(force=True)
                logger.info("Closed pool for '%s'", alias)
            except Exception:
                logger.exception("Error closing pool for '%s'", alias)
        self._pools.clear()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    @contextmanager
    def get_connection(self, alias: str) -> Generator[cx_Oracle.Connection, None, None]:
        """Acquire a connection from the pool identified by *alias*."""
        pool = self._pools.get(alias)
        if pool is None:
            # Attempt lazy init (useful for Celery workers)
            pool = self.init_pool(alias)
        conn = pool.acquire()
        try:
            yield conn
        finally:
            pool.release(conn)

    @contextmanager
    def get_cursor(self, alias: str) -> Generator[cx_Oracle.Cursor, None, None]:
        """Acquire a cursor from the pool identified by *alias*."""
        with self.get_connection(alias) as conn:
            cursor = conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_databases(self) -> list[dict[str, Any]]:
        """Return a list of configured databases (alias + description)."""
        result = []
        for alias, cfg in self._configs.items():
            result.append({
                "alias": cfg.alias,
                "description": cfg.description,
                "dsn": cfg.dsn,
                "user": cfg.user,
                "pool_min": cfg.pool_min,
                "pool_max": cfg.pool_max,
                "pool_increment": cfg.pool_increment,
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
