from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "credit_engine",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks.brasil_api",
        "app.workers.tasks.portal_transparencia",
        "app.workers.tasks.cndt_tst",
        "app.workers.tasks.cnd_federal",
        "app.workers.tasks.fgts",
        "app.workers.tasks.web_research",
        "app.workers.tasks.score_engine",
        "app.workers.tasks.orchestrator",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Filas por tipo de componente
    task_routes={
        "app.workers.tasks.brasil_api.*":           {"queue": "fast"},
        "app.workers.tasks.portal_transparencia.*": {"queue": "fast"},
        "app.workers.tasks.cndt_tst.*":             {"queue": "captcha"},
        "app.workers.tasks.web_research.*":         {"queue": "llm"},
        "app.workers.tasks.score_engine.*":         {"queue": "llm"},
        "app.workers.tasks.orchestrator.*":         {"queue": "orchestrator"},
    },

    # Retry defaults
    task_default_retry_delay=10,
    task_max_retries=3,

    # Resultados expiram em 24h
    result_expires=86400,
)
