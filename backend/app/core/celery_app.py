"""
App Celery do orchestrator.

A fila "audio_producao" roda com --concurrency=1 propositalmente: o
O Voice Service possui sua própria fila GPU serial e durável. Mantemos uma
task de workflow por vez para simplificar montagem, mastering e recuperação.
Mesmo padrão já usado no pipeline de vídeo para filas de GPU.
"""

from celery import Celery  # type: ignore[import-untyped]

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
        "app.tasks.qa_tasks.*": {"queue": "audio_producao"},
    },
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_connection_retry_on_startup=True,
)
