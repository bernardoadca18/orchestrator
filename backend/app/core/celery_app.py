"""Celery queues for serial audio work and independent render/QA control work."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "orchestrator",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.audio_edit_tasks",
        "app.tasks.audio_tasks",
        "app.tasks.production_tasks",
        "app.tasks.qa_tasks",
        "app.tasks.thumbnail_tasks",
    ],
)

celery_app.conf.update(
    task_routes={
        "app.tasks.thumbnail_tasks.*": {"queue": "audio_producao"},
        "app.tasks.audio_tasks.*": {"queue": "audio_producao"},
        "app.tasks.audio_edit_tasks.*": {"queue": "audio_producao"},
        "app.tasks.production_tasks.*": {"queue": "audio_producao"},
        "app.tasks.production_tasks.poll_video_render": {"queue": "production_control"},
        "app.tasks.qa_tasks.*": {"queue": "production_control"},
    },
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_connection_retry_on_startup=True,
)


def is_control_worker(sender: object) -> bool:
    """Keep restart reconciliation single-owned when both workers start."""
    return str(getattr(sender, "hostname", "")).startswith("control@")
