"""Oracle pool adapter using oracledb SessionPool."""

from __future__ import annotations

import logging
from typing import Any

from app.db.pool_adapter import PoolAdapter

logger = logging.getLogger(__name__)

_thick_mode_initialized = False


def _ensure_thick_mode() -> None:
    """Initialise Oracle Client (thick mode) once, if configured."""
    global _thick_mode_initialized
    if _thick_mode_initialized:
        return
    from app.core.config import settings

    import oracledb

    lib_dir = settings.oracle_client_dir or None
    try:
        oracledb.init_oracle_client(lib_dir=lib_dir)
        logger.info("Oracle thick mode enabled (lib_dir=%s)", lib_dir)
    except oracledb.ProgrammingError:
        # Already initialised (e.g. in Celery worker)
        pass
    _thick_mode_initialized = True


class OraclePoolAdapter(PoolAdapter):
    """Wraps an ``oracledb.SessionPool``."""

    def __init__(self) -> None:
        self._pool: Any = None

    def create(self, config: Any) -> None:
        import oracledb

        _ensure_thick_mode()
        self._pool = oracledb.SessionPool(
            user=config.user,
            password=config.password,
            dsn=config.dsn,
            min=config.pool_min,
            max=config.pool_max,
            increment=config.pool_increment,
            encoding="UTF-8",
            threaded=True,
            getmode=oracledb.SPOOL_ATTRVAL_WAIT,
        )

    def close(self, force: bool = False) -> None:
        if self._pool is not None:
            self._pool.close(force=force)
            self._pool = None

    def _acquire(self) -> Any:
        return self._pool.acquire()

    def _release(self, conn: Any) -> None:
        self._pool.release(conn)
