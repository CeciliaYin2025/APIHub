"""Celery tasks for asynchronous database write operations."""

import logging
from typing import Any

from app.db.oracle_pool import init_pool
from app.services.write_service import execute_batch_write, execute_write
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.write_tasks.async_write", max_retries=3)
def async_write(
    self,
    operation: str,
    table: str,
    data: list[dict[str, Any]],
    where: dict[str, Any] | None = None,
) -> dict:
    """Execute a write operation asynchronously via Celery.

    Retries up to 3 times with exponential backoff on transient failures.
    """
    # Ensure the pool is initialized in the worker process
    init_pool()
    try:
        affected = execute_write(operation, table, data, where)
        return {"success": True, "operation": operation, "affected_rows": affected}
    except Exception as exc:
        logger.exception("Async write failed (attempt %d)", self.request.retries + 1)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


@celery_app.task(bind=True, name="app.tasks.write_tasks.async_batch_write", max_retries=3)
def async_batch_write(
    self,
    table: str,
    operations: list[dict[str, Any]],
) -> dict:
    """Execute a batch transactional write asynchronously via Celery."""
    init_pool()
    try:
        result = execute_batch_write(table, operations)
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("Async batch write failed (attempt %d)", self.request.retries + 1)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
