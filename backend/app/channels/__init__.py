"""Único lugar que habla con WhatsApp (regla de oro 4).

El canal se elige con la variable de entorno WHATSAPP_CHANNEL ("openbsp" o "fake").
"""

from functools import lru_cache
from pathlib import Path

from app.channels.base import (
    Channel,
    ChannelError,
    ChannelTemporaryError,
    IncomingMessage,
    Media,
    MediaNotAvailableError,
)
from app.channels.ventana import VENTANA_ATENCION, dentro_de_ventana
from app.config import get_settings


@lru_cache
def get_channel() -> Channel:
    settings = get_settings()
    if settings.whatsapp_channel == "openbsp":
        from app.channels.openbsp import OpenBSPChannel

        return OpenBSPChannel.from_settings(settings)
    from app.channels.fake import FakeChannel

    return FakeChannel(Path(settings.fake_media_dir))


__all__ = [
    "Channel",
    "ChannelError",
    "ChannelTemporaryError",
    "IncomingMessage",
    "Media",
    "MediaNotAvailableError",
    "VENTANA_ATENCION",
    "dentro_de_ventana",
    "get_channel",
]
