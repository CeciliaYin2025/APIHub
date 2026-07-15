"""Live connectivity checks for the configured data-source databases."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.db.dialect_registry import get_dialect
from app.db.multi_pool import db_registry

logger = logging.getLogger(__name__)

# Bounds concurrency WITHIN a single check. Note this executor is created per
# call, so it is not a cumulative ceiling: each refresh against a hung database
# abandons up to this many threads (shutdown(wait=False) below), and they live
# until the driver's own network timeout fires. Accepted trade-off — see spec §6.
_MAX_WORKERS = 8


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


def check_all_databases() -> list[dict[str, Any]]:
    """Probe every configured database concurrently under one shared deadline.

    The drivers are blocking, so concurrency requires threads. All aliases
    share a single ``db_health_check_timeout`` budget rather than each getting
    their own, so N dead databases cost N-times-nothing extra in wall clock.
    """
    aliases = db_registry.aliases()
    if not aliases:
        return []

    timeout = settings.db_health_check_timeout
    executor = ThreadPoolExecutor(max_workers=min(len(aliases), _MAX_WORKERS))
    try:
        futures = {alias: executor.submit(check_database, alias) for alias in aliases}
        deadline = time.perf_counter() + timeout

        results: list[dict[str, Any]] = []
        for alias, future in futures.items():
            remaining = max(0.0, deadline - time.perf_counter())
            try:
                results.append(future.result(timeout=remaining))
            except FutureTimeoutError:
                future.cancel()
                logger.warning("Health check for '%s' exceeded %.1fs", alias, timeout)
                results.append({
                    "alias": alias,
                    "status": "timeout",
                    "latency_ms": None,
                    "error": f"Health check exceeded {timeout}s",
                    "checked_at": _now_iso(),
                })
        return results
    finally:
        # Do not join abandoned workers — a hung driver call would block the
        # response until its own network timeout fires. cancel_futures drops
        # any aliases still queued (relevant when len(aliases) > _MAX_WORKERS
        # and the deadline has already expired) so they don't open pointless
        # connections after we've already returned.
        executor.shutdown(wait=False, cancel_futures=True)
