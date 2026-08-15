"""
App Celery do orchestrator.

A fila "audio_producao" roda com --concurrency=1 propositalmente: o
voice-workshop só processa uma narração por vez (lock de GPU), então
paralelizar workers aqui só geraria erro 500 do lado do voice-workshop.
Mesmo padrão já usado no pipeline de vídeo para filas de GPU.
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "orchestrator",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.audio_tasks"],
)

celery_app.conf.update(
    task_routes={"app.tasks.audio_tasks.*": {"queue": "audio_producao"}},
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
