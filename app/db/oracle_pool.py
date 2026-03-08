"""Connection pool management — backward-compatible helper functions.

This module delegates to :pymod:`app.db.multi_pool` for actual pool management.
The legacy helper functions (``init_pool``, ``close_pool``, ``get_connection``,
``get_cursor``) are retained for backward compatibility and always operate on
the **first registered database** (or the ``default`` alias when using the
legacy single-DB environment variable).

Also provides ``get_dialect_for()`` to resolve the dialect for a given alias.
"""

import logging
from contextlib import contextmanager
from typing import Any, Generator

from app.db.dialect import Dialect
from app.db.dialect_registry import get_dialect
from app.db.multi_pool import db_registry

logger = logging.getLogger(__name__)


def _default_alias() -> str:
    """Return the first (or only) registered database alias."""
    aliases = db_registry.aliases()
    if not aliases:
        raise RuntimeError("No database configurations loaded. Check database_definitions_dir.")
    return aliases[0]


def init_pool() -> None:
    """Initialise pools for **all** registered databases.

    Called at application startup and by Celery workers before executing
    their first task.
    """
    # Ensure configs are loaded (idempotent — skips if already loaded).
    if not db_registry.aliases():
        db_registry.load()
    db_registry.init_all_pools()


def close_pool() -> None:
    """Close every managed pool. Called at application shutdown."""
    db_registry.close_all_pools()


@contextmanager
def get_connection(db_alias: str | None = None) -> Generator[Any, None, None]:
    """Acquire a connection from the pool identified by *db_alias*.

    If *db_alias* is ``None``, the first registered database is used
    (backward-compatible behaviour).
    """
    alias = db_alias or _default_alias()
    with db_registry.get_connection(alias) as conn:
        yield conn


@contextmanager
def get_cursor(db_alias: str | None = None) -> Generator[Any, None, None]:
    """Convenience: acquire a connection and return a cursor."""
    with get_connection(db_alias) as conn:
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()


def get_dialect_for(db_alias: str | None = None) -> Dialect:
    """Return the dialect for *db_alias* (resolves default if ``None``)."""
    alias = db_alias or _default_alias()
    db_type = db_registry.get_db_type(alias)
    return get_dialect(db_type)
