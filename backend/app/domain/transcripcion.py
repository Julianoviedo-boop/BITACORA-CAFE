"""Caso de uso: transcribir el audio de un mensaje.

Pasos (cada uno deja su duración en el log):
  1. tomar:      lee el mensaje (transacción corta) y verifica que toca transcribirlo.
  2. descargar:  pide el archivo al canal. Sin transacción abierta.
  3. transcribir: archivo temporal -> whisper. El temporal se borra siempre.
  4. guardar:    guarda el texto y pasa el mensaje a "transcrito" (transacción corta).

No se deja una transacción abierta durante la descarga ni la transcripción: pueden
tardar más de un minuto.

Las fallas temporales de descarga (ChannelTemporaryError) se dejan subir para que
la tarea de Celery reintente. Las definitivas dejan el mensaje en "error" y
devuelven el aviso para el productor; el aviso lo manda otra tarea, para que una
falla al enviarlo no obligue a transcribir de nuevo.

El máximo de duración y los textos de los avisos están en config/transcripcion.yaml.
"""

from __future__ import annotations

import contextlib
import logging
import mimetypes
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import ContextManager, Literal

import yaml

from app.channels.base import Channel, MediaNotAvailableError
from app.config import get_settings
from app.db.repositories import Repositories
from app.transcription.whisper import (
    AudioDemasiadoLargoError,
    AudioInvalidoError,
    Transcripcion,
)

logger = logging.getLogger(__name__)

ClaveAviso = Literal["audio_muy_largo", "audio_no_disponible", "audio_sin_voz",
                     "error_transcripcion"]
CLAVES_AVISO: tuple[ClaveAviso, ...] = ("audio_muy_largo", "audio_no_disponible",
                                        "audio_sin_voz", "error_transcripcion")

UnitOfWork = Callable[[], ContextManager[Repositories]]
# Recibe la ruta del archivo y el máximo en segundos.
Transcriptor = Callable[[Path, float], Transcripcion]


# ---------------------------------------------------------------------------
# Reglas (config/transcripcion.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReglasTranscripcion:
    max_duracion_s: int
    avisos: dict[str, str]

    def aviso(self, clave: ClaveAviso) -> str:
        minutos = self.max_duracion_s / 60
        minutos_txt = f"{minutos:g}".replace(".", ",")
        return self.avisos[clave].replace("{minutos}", minutos_txt).strip()


def cargar_reglas(config_dir: str | Path) -> ReglasTranscripcion:
    path = Path(config_dir) / "transcripcion.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    maximo = data.get("max_duracion_segundos")
    if not isinstance(maximo, int) or isinstance(maximo, bool) or maximo <= 0:
        raise ValueError(f"{path}: max_duracion_segundos debe ser un entero mayor que 0")
    avisos = data.get("avisos") or {}
    faltan = [c for c in CLAVES_AVISO if not str(avisos.get(c) or "").strip()]
    if faltan:
        raise ValueError(f"{path}: faltan los avisos {', '.join(faltan)}")
    return ReglasTranscripcion(max_duracion_s=maximo, avisos={c: str(avisos[c]) for c in CLAVES_AVISO})


@lru_cache
def get_reglas() -> ReglasTranscripcion:
    return cargar_reglas(get_settings().config_dir)


# ---------------------------------------------------------------------------
# Resultado y medición
# ---------------------------------------------------------------------------

Estado = Literal["transcrito", "omitido", "fallido"]


@dataclass(frozen=True)
class Resultado:
    estado: Estado
    # Texto para el productor cuando estado == "fallido".
    aviso: str | None = None
    detalle: str | None = None


@contextlib.contextmanager
def medir(paso: str, message_id: uuid.UUID, **extra: object) -> Iterator[None]:
    t0 = time.perf_counter()
    ok = False
    try:
        yield
        ok = True
    finally:
        datos = " ".join(f"{k}={v}" for k, v in extra.items())
        logger.info("transcripcion msg=%s paso=%s ms=%d ok=%s %s", message_id, paso,
                    (time.perf_counter() - t0) * 1000, ok, datos)


# ---------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------


def tomar_audio(
    repos: Repositories, organization_id: uuid.UUID, message_id: uuid.UUID
) -> str | None:
    """Devuelve la referencia del audio si el mensaje está listo para transcribir; si no, None.

    Se vuelve a revisar el consentimiento: entre el encolado y la ejecución el
    productor pudo haberlo revocado.
    """
    msg = repos.messages.get(organization_id, message_id)
    if (msg is None or msg.message_type != "audio" or msg.processing_status != "recibido"
            or not msg.media_ref or msg.producer_id is None):
        return None
    producer = repos.producers.get(organization_id, msg.producer_id)
    if producer is None or producer.consent_status != "aceptado":
        return None
    return msg.media_ref


def guardar_transcripcion(
    repos: Repositories, organization_id: uuid.UUID, message_id: uuid.UUID, texto: str
) -> bool:
    """Guarda el texto. Si otro worker ya lo hizo (el estado no es "recibido"), no hace nada."""
    if repos.messages.set_processing_status(organization_id, message_id, "transcrito",
                                            expected="recibido") is None:
        return False
    repos.messages.update(organization_id, message_id, transcription=texto)
    return True


def registrar_fallo(
    uow: UnitOfWork,
    reglas: ReglasTranscripcion,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    clave: ClaveAviso,
    detalle: str,
) -> Resultado:
    """Deja el mensaje en "error" y devuelve el aviso. Si el mensaje ya no estaba en
    "recibido" (otro worker lo atendió), no avisa: así no se avisa dos veces."""
    with uow() as repos:
        marcado = repos.messages.set_processing_status(
            organization_id, message_id, "error", error_detail=f"{clave}: {detalle}"[:1000],
            expected="recibido",
        )
    if marcado is None:
        return Resultado("omitido", detalle=detalle)
    logger.warning("transcripcion msg=%s fallo=%s detalle=%s", message_id, clave, detalle)
    return Resultado("fallido", aviso=reglas.aviso(clave), detalle=clave)


def _sufijo(media_ref: str, mime_type: str | None) -> str:
    if mime_type:
        ext = mimetypes.guess_extension(mime_type.split(";")[0].strip())
        if ext:
            return ext
    return Path(media_ref).suffix[:10] or ".audio"


@contextlib.contextmanager
def archivo_temporal(contenido: bytes, sufijo: str) -> Iterator[Path]:
    """Escribe el contenido en un archivo temporal y lo borra al salir, pase lo que pase."""
    fd, nombre = tempfile.mkstemp(prefix="audio-", suffix=sufijo)
    path = Path(nombre)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contenido)
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def transcribir_mensaje(
    uow: UnitOfWork,
    channel: Channel,
    transcriptor: Transcriptor,
    reglas: ReglasTranscripcion,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
) -> Resultado:
    t_total = time.perf_counter()

    with medir("tomar", message_id), uow() as repos:
        media_ref = tomar_audio(repos, organization_id, message_id)
    if media_ref is None:
        return Resultado("omitido")

    try:
        # ChannelTemporaryError sube: la tarea reintenta.
        with medir("descargar", message_id):
            media = channel.download_media(media_ref)
    except MediaNotAvailableError as exc:
        return registrar_fallo(uow, reglas, organization_id, message_id,
                               "audio_no_disponible", str(exc))

    sufijo = _sufijo(media_ref, media.mime_type)
    try:
        with archivo_temporal(media.content, sufijo) as path, \
                medir("transcribir", message_id, bytes=len(media.content), tipo=sufijo):
            t = transcriptor(path, reglas.max_duracion_s)
    except AudioDemasiadoLargoError as exc:
        return registrar_fallo(uow, reglas, organization_id, message_id,
                               "audio_muy_largo", str(exc))
    except AudioInvalidoError as exc:
        return registrar_fallo(uow, reglas, organization_id, message_id,
                               "audio_no_disponible", str(exc))
    except Exception as exc:  # noqa: BLE001 - un error inesperado no debe dejar al productor sin respuesta
        logger.exception("transcripcion msg=%s error inesperado", message_id)
        return registrar_fallo(uow, reglas, organization_id, message_id,
                               "error_transcripcion", f"{type(exc).__name__}: {exc}")

    logger.info("transcripcion msg=%s audio_s=%.1f confianza=%.3f caracteres=%d tiempos=%s",
                message_id, t.duracion_s, t.confianza, len(t.texto), t.tiempos)

    if not t.texto:
        return registrar_fallo(uow, reglas, organization_id, message_id,
                               "audio_sin_voz", f"Sin voz en {t.duracion_s:.0f} s de audio")

    with medir("guardar", message_id), uow() as repos:
        guardado = guardar_transcripcion(repos, organization_id, message_id, t.texto)

    logger.info("transcripcion msg=%s paso=total ms=%d", message_id,
                (time.perf_counter() - t_total) * 1000)
    return Resultado("transcrito") if guardado else Resultado("omitido")


def enviar_aviso(
    repos: Repositories,
    channel: Channel,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    texto: str,
) -> bool:
    """Le manda `texto` al productor que envió el mensaje. False si no hay a quién."""
    msg = repos.messages.get(organization_id, message_id)
    org = repos.organizations.get(organization_id)
    if msg is None or org is None or not org.whatsapp_address:
        return False
    channel.send_text(org.whatsapp_address, msg.from_phone, texto)
    return True
