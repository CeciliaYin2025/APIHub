"""Oracle connection pool management using cx_Oracle.

Uses cx_Oracle SessionPool for efficient connection reuse, which is critical
for Oracle 11g compatibility (no async driver available).
"""

import logging
from contextlib import contextmanager
from typing import Generator

import cx_Oracle

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: cx_Oracle.SessionPool | None = None


def init_pool() -> cx_Oracle.SessionPool:
    """Initialize the Oracle connection pool. Called at application startup."""
    global _pool
    if _pool is not None:
        return _pool

    logger.info("Initializing Oracle connection pool (DSN: %s)", settings.oracle_dsn)
    _pool = cx_Oracle.SessionPool(
        user=settings.oracle_user,
        password=settings.oracle_password,
        dsn=settings.oracle_dsn,
        min=settings.oracle_pool_min,
        max=settings.oracle_pool_max,
        increment=settings.oracle_pool_increment,
        encoding="UTF-8",
        threaded=True,
        getmode=cx_Oracle.SPOOL_ATTRVAL_WAIT,
    )
    logger.info("Oracle pool created (min=%d, max=%d)", settings.oracle_pool_min, settings.oracle_pool_max)
    return _pool


def close_pool() -> None:
    """Close the Oracle connection pool. Called at application shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("Oracle pool closed")


@contextmanager
def get_connection() -> Generator[cx_Oracle.Connection, None, None]:
    """Acquire a connection from the pool and release it when done.

    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM DUAL")
    """
    if _pool is None:
        raise RuntimeError("Oracle pool is not initialized. Call init_pool() first.")
    conn = _pool.acquire()
    try:
        yield conn
    finally:
        _pool.release(conn)


@contextmanager
def get_cursor() -> Generator[cx_Oracle.Cursor, None, None]:
    """Convenience: acquire a connection and return a cursor."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()
