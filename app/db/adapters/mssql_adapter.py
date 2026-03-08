"""MS SQL Server pool adapter using pymssql with a queue-based pool."""

from __future__ import annotations

import logging
import queue
from typing import Any

from app.db.pool_adapter import PoolAdapter

logger = logging.getLogger(__name__)


class MssqlPoolAdapter(PoolAdapter):
    """Thread-safe connection pool for pymssql (no built-in pooling)."""

    def __init__(self) -> None:
        self._pool: queue.Queue[Any] = queue.Queue()
        self._config: Any = None

    def create(self, config: Any) -> None:
        import pymssql

        self._config = config
        for _ in range(config.pool_min):
            conn = pymssql.connect(
                server=config.host,
                port=config.port,
                database=config.database,
                user=config.user,
                password=config.password,
                autocommit=False,
            )
            self._pool.put(conn)

    def close(self, force: bool = False) -> None:
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except (queue.Empty, Exception):
                break
        self._config = None

    def _acquire(self) -> Any:
        try:
            conn = self._pool.get_nowait()
            # Verify the connection is still alive
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                return self._create_connection()
        except queue.Empty:
            return self._create_connection()

    def _release(self, conn: Any) -> None:
        self._pool.put(conn)

    def _create_connection(self) -> Any:
        import pymssql

        return pymssql.connect(
            server=self._config.host,
            port=self._config.port,
            database=self._config.database,
            user=self._config.user,
            password=self._config.password,
            autocommit=False,
        )
