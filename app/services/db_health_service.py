"""Live connectivity checks for the configured data-source databases."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.db.dialect_registry import get_dialect
from app.db.multi_pool import db_registry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def check_database(alias: str) -> dict[str, Any]:
    """Probe *alias* with a trivial query and report its connectivity.

    Never raises: any driver/config failure is reported as ``offline`` so one
    dead data source cannot break the status of the others.
    """
    started = time.perf_counter()
    try:
        dialect = get_dialect(db_registry.get_db_type(alias))
        with db_registry.get_cursor(alias) as cursor:
            cursor.execute(dialect.health_check_sql())
            cursor.fetchone()
    except Exception as exc:
        # Includes the case where the pool failed at startup and init_pool
        # is retried (and re-fails) inside get_cursor.
        logger.warning("Health check failed for '%s': %s", alias, exc)
        return {
            "alias": alias,
            "status": "offline",
            "latency_ms": None,
            "error": str(exc),
            "checked_at": _now_iso(),
        }

    return {
        "alias": alias,
        "status": "online",
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": None,
        "checked_at": _now_iso(),
    }
