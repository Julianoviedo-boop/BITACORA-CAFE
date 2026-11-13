"""Tareas de Celery."""

import logging
import uuid
from pathlib import Path

from celery.signals import worker_process_init

from app.channels import ChannelTemporaryError, get_channel
from app.db.repositories import unit_of_work
from app.domain import transcripcion
from app.domain.consentimiento import atender_mensaje, get_textos
from app.transcription import whisper
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Reintentos de la descarga del audio: 30 s, 60 s, 120 s, 240 s, 300 s (~12 min en total).
DESCARGA_MAX_REINTENTOS = 5
DESCARGA_ESPERA_BASE_S = 30
DESCARGA_ESPERA_MAX_S = 300


@worker_process_init.connect
def precargar_modelo(**_: object) -> None:
    """Carga whisper al arrancar cada proceso del worker (una vez, no por tarea).

    Si falla (p. ej. sin internet para bajar el modelo), el worker sigue vivo y
    la carga se reintenta en la primera tarea.
    """
    try:
        whisper.modelo_desde_settings()
        whisper.prompt_desde_config()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo precargar el modelo de whisper")


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
        resultado = atender_mensaje(
            repos, get_channel(), uuid.UUID(organization_id), uuid.UUID(message_id), get_textos()
        )
    # Se encola después del commit, para que la otra tarea vea el mensaje.
    if resultado == "listo_para_transcribir":
        transcribir_audio.delay(organization_id, message_id)
    return resultado


def _transcriptor(path: Path, max_duracion_s: float) -> whisper.Transcripcion:
    return whisper.transcribir(path, max_duracion_s=max_duracion_s)


def _espera_descarga(reintento: int) -> int:
    return min(DESCARGA_ESPERA_BASE_S * 2**reintento, DESCARGA_ESPERA_MAX_S)


@celery_app.task(name="transcribir_audio", bind=True, max_retries=DESCARGA_MAX_REINTENTOS)
def transcribir_audio(self, organization_id: str, message_id: str) -> str:
    """Descarga y transcribe el audio de un mensaje.

    Reintenta solo las fallas temporales de descarga. Cuando se acaban los
    reintentos, o la falla es definitiva, avisa al productor por el canal.
    """
    org_id, msg_id = uuid.UUID(organization_id), uuid.UUID(message_id)
    reglas = transcripcion.get_reglas()
    try:
        resultado = transcripcion.transcribir_mensaje(
            unit_of_work, get_channel(), _transcriptor, reglas, org_id, msg_id
        )
    except ChannelTemporaryError as exc:
        if self.request.retries < self.max_retries:
            espera = _espera_descarga(self.request.retries)
            logger.warning("transcripcion msg=%s descarga fallida (intento %d), reintento en %d s: %s",
                           message_id, self.request.retries + 1, espera, exc)
            raise self.retry(exc=exc, countdown=espera) from exc
        resultado = transcripcion.registrar_fallo(
            unit_of_work, reglas, org_id, msg_id, "audio_no_disponible",
            f"Descarga fallida tras {self.request.retries + 1} intentos: {exc}",
        )

    if resultado.aviso:
        avisar_productor.delay(organization_id, message_id, resultado.aviso)
    return resultado.estado


@celery_app.task(
    name="avisar_productor",
    autoretry_for=(ChannelTemporaryError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=5,
)
def avisar_productor(organization_id: str, message_id: str, texto: str) -> bool:
    """Le manda un aviso al productor que envió el mensaje."""
    with unit_of_work() as repos:
        return transcripcion.enviar_aviso(
            repos, get_channel(), uuid.UUID(organization_id), uuid.UUID(message_id), texto
        )

