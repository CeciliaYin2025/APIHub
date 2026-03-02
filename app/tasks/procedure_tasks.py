"""Celery tasks for asynchronous Oracle procedure/function execution."""

import logging
from typing import Any

from app.db.oracle_pool import init_pool
from app.services.procedure_service import execute_procedure
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.procedure_tasks.async_procedure", max_retries=3)
def async_procedure(
    self,
    name: str,
    params: dict[str, Any],
    out_params: list[str] | None = None,
    is_function: bool = False,
    db_alias: str | None = None,
) -> dict:
    """Execute a procedure/function asynchronously via Celery."""
    init_pool()
    try:
        result = execute_procedure(name, params, out_params, is_function, db_alias=db_alias)
        return {"success": True, "name": name, **result}
    except Exception as exc:
        logger.exception("Async procedure call failed (attempt %d)", self.request.retries + 1)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
