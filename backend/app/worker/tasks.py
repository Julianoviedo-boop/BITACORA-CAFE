"""Tareas de Celery."""

import uuid

from app.channels import ChannelTemporaryError, get_channel
from app.db.repositories import unit_of_work
from app.domain.consentimiento import atender_mensaje, get_textos
from app.worker.celery_app import celery_app


@celery_app.task(name="ping")
def ping() -> str:
    """Tarea mínima para comprobar que la cola funciona de punta a punta."""
    return "pong"


@celery_app.task(
    name="procesar_mensaje",
    autoretry_for=(ChannelTemporaryError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=5,
)
def procesar_mensaje(organization_id: str, message_id: str) -> str:
    """Atiende un mensaje guardado por el webhook.

    El envío por WhatsApp ocurre dentro de la transacción: si falla, no queda
    ningún cambio en la base y la tarea se reintenta desde cero.
    """
    with unit_of_work() as repos:
        return atender_mensaje(
            repos, get_channel(), uuid.UUID(organization_id), uuid.UUID(message_id), get_textos()
        )
