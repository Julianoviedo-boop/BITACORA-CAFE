"""Interfaz del canal de WhatsApp.

El resto del sistema (webhook, dominio, worker) solo conoce esta interfaz y los
tipos de este archivo; los detalles de cada proveedor viven en su módulo
(openbsp.py, fake.py). Regla de oro 4.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

MessageKind = Literal["texto", "audio", "imagen", "otro"]


class ChannelError(Exception):
    """Falla hablando con el canal que no se arregla reintentando (configuración,
    petición rechazada)."""


class ChannelTemporaryError(ChannelError):
    """Falla de red o del proveedor (5xx): vale la pena reintentar."""


class MediaNotAvailableError(ChannelError):
    """El archivo no se puede descargar (referencia no soportada, muy grande, no existe)."""


@dataclass(frozen=True)
class IncomingMessage:
    """Mensaje entrante ya normalizado, sin detalles del proveedor."""

    external_id: str
    # Número que recibió el mensaje, tal como lo identifica el canal. Con él se
    # resuelve la organización (organizations.whatsapp_address).
    channel_address: str
    # Teléfono de quien escribió, en E.164 con "+": +573001234567.
    sender_phone: str
    kind: MessageKind
    text: str | None = None
    media_ref: str | None = None
    media_mime_type: str | None = None


@dataclass(frozen=True)
class Media:
    content: bytes
    mime_type: str | None = None


class Channel(ABC):
    @abstractmethod
    def parse_incoming(self, payload: Any) -> IncomingMessage | None:
        """Convierte el cuerpo del webhook en un IncomingMessage.

        Devuelve None si el aviso no es un mensaje nuevo de un contacto (mensajes
        propios, cambios de estado, otras tablas, grupos...). Nunca lanza por un
        payload raro: lo que no se entiende se ignora o queda como kind="otro".
        """

    @abstractmethod
    def download_media(self, media_ref: str) -> Media:
        """Descarga el archivo de un mensaje (audio, imagen). Lanza MediaNotAvailableError."""

    @abstractmethod
    def send_text(self, channel_address: str, to_phone: str, text: str) -> None:
        """Envía un texto desde el número `channel_address` al teléfono E.164 `to_phone`."""
