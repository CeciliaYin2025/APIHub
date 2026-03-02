"""Celery application instance and configuration."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "oracle_api_hub",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # one task at a time per worker — protects DB
    task_default_queue="default",
    task_routes={
        "app.tasks.write_tasks.async_write": {"queue": "write"},
        "app.tasks.write_tasks.async_batch_write": {"queue": "write"},
        "app.tasks.procedure_tasks.async_procedure": {"queue": "procedure"},
    },
)

celery_app.autodiscover_tasks(["app.tasks"])
