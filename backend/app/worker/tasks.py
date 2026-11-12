"""Tareas de Celery."""

from app.worker.celery_app import celery_app


@celery_app.task(name="ping")
def ping() -> str:
    """Tarea mínima para comprobar que la cola funciona de punta a punta."""
    return "pong"
