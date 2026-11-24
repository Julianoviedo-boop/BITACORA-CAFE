"""Canal falso para desarrollo y pruebas: no habla con WhatsApp.

- parse_incoming: entiende el mismo formato que OpenBSP, así el webhook y
  scripts/simular_mensaje.py ejercitan el camino real.
- download_media: lee archivos locales con referencias `fixture://<nombre>`,
  desde FAKE_MEDIA_DIR (por defecto backend/tests/fixtures).
- send_text: no envía; guarda el mensaje en `sent` y lo escribe en el log.
- send_template: igual, en `templates`.
"""

import logging
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.channels.base import Channel, IncomingMessage, Media, MediaNotAvailableError
from app.channels.openbsp import parse_openbsp_payload

logger = logging.getLogger(__name__)

FIXTURE_PREFIX = "fixture://"

# Tipos de audio de WhatsApp que Python no siempre conoce (depende de /etc/mime.types).
for _tipo, _ext in (("audio/ogg", ".ogg"), ("audio/ogg", ".opus"), ("audio/mp4", ".m4a"),
                    ("audio/amr", ".amr")):
    mimetypes.add_type(_tipo, _ext)


@dataclass(frozen=True)
class SentText:
    channel_address: str
    to_phone: str
    text: str


@dataclass(frozen=True)
class SentTemplate:
    channel_address: str
    to_phone: str
    template: str
    language: str
    params: tuple[str, ...]


@dataclass(frozen=True)
class SentDocument:
    channel_address: str
    to_phone: str
    content: bytes
    filename: str
    mime_type: str
    caption: str | None


class FakeChannel(Channel):
    def __init__(self, media_dir: Path) -> None:
        self.media_dir = media_dir
        self.sent: list[SentText] = []
        self.templates: list[SentTemplate] = []
        self.documents: list[SentDocument] = []

    def parse_incoming(self, payload: Any) -> IncomingMessage | None:
        return parse_openbsp_payload(payload)

    def download_media(self, media_ref: str) -> Media:
        if not media_ref.startswith(FIXTURE_PREFIX):
            raise MediaNotAvailableError("El canal falso solo lee referencias fixture://")
        base = self.media_dir.resolve()
        path = (base / media_ref.removeprefix(FIXTURE_PREFIX)).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise MediaNotAvailableError(f"No existe el archivo de prueba {media_ref}")
        return Media(path.read_bytes(), mimetypes.guess_type(path.name)[0])

    def send_text(self, channel_address: str, to_phone: str, text: str) -> None:
        self.sent.append(SentText(channel_address, to_phone, text))
        logger.info("[canal falso] %s -> %s: %s", channel_address, to_phone, text)

    def send_template(
        self, channel_address: str, to_phone: str, template: str, language: str,
        params: Sequence[str],
    ) -> None:
        self.templates.append(
            SentTemplate(channel_address, to_phone, template, language, tuple(params))
        )
        logger.info("[canal falso] %s -> %s: plantilla %s (%s) %s", channel_address, to_phone,
                    template, language, list(params))

    def send_document(
        self, channel_address: str, to_phone: str, content: bytes, filename: str,
        mime_type: str, caption: str | None = None,
    ) -> None:
        self.documents.append(
            SentDocument(channel_address, to_phone, content, filename, mime_type, caption)
        )
        logger.info("[canal falso] %s -> %s: documento %s (%s, %d bytes) %s", channel_address,
                    to_phone, filename, mime_type, len(content), caption or "")
