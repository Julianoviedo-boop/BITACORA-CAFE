"""Tareas de Celery."""

import datetime as dt
import logging
import uuid
from functools import lru_cache
from pathlib import Path

from celery.signals import worker_process_init

from app.channels import ChannelTemporaryError, get_channel
from app.db.repositories import unit_of_work
from app.domain import alertas, conversacion, transcripcion
from app.domain.consentimiento import atender_mensaje, get_textos
from app.extraction.llm import ClienteLLM
from app.extraction.schema import catalogo_por_defecto
from app.transcription import whisper
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Reintentos de la descarga del audio: 30 s, 60 s, 120 s, 240 s, 300 s (~12 min en total).
DESCARGA_MAX_REINTENTOS = 5
DESCARGA_ESPERA_BASE_S = 30
DESCARGA_ESPERA_MAX_S = 300
# Reintentos de la conversación (Claude caído o conversación ocupada); misma espera.
CONVERSACION_MAX_REINTENTOS = 5


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
    elif resultado == "listo_para_procesar":
        atender_conversacion.delay(organization_id, message_id)
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
    elif resultado.estado == "transcrito":
        atender_conversacion.delay(organization_id, message_id)
    return resultado.estado


@lru_cache
def _cliente_llm() -> ClienteLLM:
    # Uno por proceso: reutiliza las conexiones HTTP con la API de Claude.
    return ClienteLLM()


def _interprete() -> conversacion.Interprete:
    return conversacion.Interprete(catalogo=catalogo_por_defecto(), cliente=_cliente_llm())


@celery_app.task(name="atender_conversacion", bind=True, max_retries=CONVERSACION_MAX_REINTENTOS)
def atender_conversacion(self, organization_id: str, message_id: str) -> str:
    """Lleva la conversación con el productor: extracción, preguntas, resumen y
    confirmación (app/domain/conversacion.py).

    Reintenta las fallas temporales (Claude o el canal). Si Claude sigue fallando
    al final de los reintentos, le avisa al productor que lo mande otra vez.
    """
    org_id, msg_id = uuid.UUID(organization_id), uuid.UUID(message_id)
    textos = conversacion.get_textos()
    try:
        return conversacion.atender(unit_of_work, get_channel(), _interprete(), textos,
                                    org_id, msg_id,
                                    despachar_alertas=_despachar_alertas(organization_id))
    except (ChannelTemporaryError, conversacion.ReintentarError) as exc:
        if self.request.retries < self.max_retries:
            espera = _espera_descarga(self.request.retries)
            logger.warning("conversacion msg=%s falla temporal (intento %d), reintento en %d s: %s",
                           message_id, self.request.retries + 1, espera, exc)
            raise self.retry(exc=exc, countdown=espera) from exc
        if isinstance(exc, ChannelTemporaryError):
            raise  # el canal sigue caído: tampoco se le puede avisar
        return conversacion.fallar(unit_of_work, get_channel(), textos, org_id, msg_id,
                                   f"Falla temporal tras {self.request.retries + 1} intentos: {exc}")


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


def _despachar_alertas(organization_id: str) -> conversacion.DespacharAlertas:
    def despachar(alerta_ids: list[uuid.UUID]) -> None:
        for alerta_id in alerta_ids:
            enviar_alerta.delay(organization_id, str(alerta_id))
    return despachar


@celery_app.task(
    name="enviar_alerta",
    autoretry_for=(ChannelTemporaryError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=5,
)
def enviar_alerta(organization_id: str, alert_id: str) -> str:
    """Manda una alerta nueva por WhatsApp (texto, plantilla o solo panel).

    Si el canal falla de forma temporal, la transacción se deshace y se reintenta;
    la alerta solo queda "enviada" cuando el envío salió.
    """
    with unit_of_work() as repos:
        return alertas.enviar(repos, get_channel(), alertas.get_reglas(),
                              uuid.UUID(organization_id), uuid.UUID(alert_id),
                              dt.datetime.now(dt.UTC))
