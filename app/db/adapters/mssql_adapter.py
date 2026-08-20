"""MS SQL Server pool adapter using pymssql with a queue-based pool."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from app.db.pool_adapter import PoolAdapter, PoolExhaustedError

logger = logging.getLogger(__name__)


class MssqlPoolAdapter(PoolAdapter):
    """Thread-safe, *bounded* connection pool for pymssql (no built-in pooling).

    The queue holds idle connections; a semaphore bounds how many can be checked
    out at once, so ``pool_max`` is a real ceiling rather than a hint. Without
    the bound, a burst of concurrent calls simply opened a new connection each
    time and the pool grew without limit — the surplus connections then queued
    on the SQL Server itself, where nothing times them out.

    A caller that finds the pool full waits at most ``pool_acquire_timeout``
    seconds and is then refused with :class:`PoolExhaustedError`. Refusing fast
    is the point: query endpoints are sync, so every waiter occupies one of the
    process's worker threads, and an unbounded wait lets one saturated alias
    starve every other endpoint — login included.
    """

    def __init__(self) -> None:
        self._pool: queue.Queue[Any] = queue.Queue()
        self._config: Any = None
        self._slots: threading.Semaphore | None = None
        self._acquire_timeout: float = 10.0

    def create(self, config: Any) -> None:
        self._config = config
        pool_max = max(1, int(getattr(config, "pool_max", 10) or 10))
        self._slots = threading.Semaphore(pool_max)
        self._acquire_timeout = float(getattr(config, "pool_acquire_timeout", 10.0) or 10.0)
        for _ in range(min(int(getattr(config, "pool_min", 0) or 0), pool_max)):
            self._pool.put(self._create_connection())

    def close(self, force: bool = False) -> None:
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except (queue.Empty, Exception):
                break
        self._config = None
        self._slots = None

    def _acquire(self) -> Any:
        if self._slots is not None and not self._slots.acquire(timeout=self._acquire_timeout):
            raise PoolExhaustedError(
                f"No MSSQL connection available for '{getattr(self._config, 'alias', '?')}' "
                f"within {self._acquire_timeout:g}s (pool_max={getattr(self._config, 'pool_max', '?')})"
            )
        try:
            return self._checkout()
        except Exception:
            # Never leak the slot when opening the connection fails, or the
            # ceiling ratchets down one connection at a time until the pool
            # refuses everything.
            if self._slots is not None:
                self._slots.release()
            raise

    def _checkout(self) -> Any:
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
        try:
            self._pool.put(conn)
        finally:
            if self._slots is not None:
                self._slots.release()

    def _create_connection(self) -> Any:
        import pymssql

        kwargs: dict[str, Any] = dict(
            server=self._config.host,
            port=self._config.port,
            database=self._config.database,
            user=self._config.user,
            password=self._config.password,
            autocommit=False,
        )
        # Pin the TDS protocol version when configured (e.g. "7.0" for legacy
        # SQL Server 2005, whose login otherwise hangs under FreeTDS auto-negotiation).
        tds_version = getattr(self._config, "tds_version", "")
        if tds_version:
            kwargs["tds_version"] = tds_version
        return pymssql.connect(**kwargs)
