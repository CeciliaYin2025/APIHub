"""PostgreSQL pool adapter using psycopg2 ThreadedConnectionPool."""

from __future__ import annotations

import logging
from typing import Any

from app.db.pool_adapter import PoolAdapter

logger = logging.getLogger(__name__)


class PostgresPoolAdapter(PoolAdapter):
    """Wraps a ``psycopg2.pool.ThreadedConnectionPool``."""

    def __init__(self) -> None:
        self._pool: Any = None

    def create(self, config: Any) -> None:
        from psycopg2.pool import ThreadedConnectionPool

        self._pool = ThreadedConnectionPool(
            minconn=config.pool_min,
            maxconn=config.pool_max,
            host=config.host,
            port=config.port,
            database=config.database,
            user=config.user,
            password=config.password,
        )

    def close(self, force: bool = False) -> None:
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None

    def _acquire(self) -> Any:
        conn = self._pool.getconn()
        conn.autocommit = False
        return conn

    def _release(self, conn: Any) -> None:
        self._pool.putconn(conn)
