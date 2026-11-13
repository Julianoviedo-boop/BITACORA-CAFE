"""Canal falso y selección del canal por variable de entorno."""

from pathlib import Path

import pytest

from app import channels
from app.channels.base import MediaNotAvailableError
from app.channels.fake import FakeChannel, SentText
from app.channels.openbsp import OpenBSPChannel
from app.config import Settings


@pytest.fixture
def canal(tmp_path: Path) -> FakeChannel:
    (tmp_path / "nota.ogg").write_bytes(b"OggS-audio")
    (tmp_path.parent / "secreto.txt").write_text("no")
    return FakeChannel(tmp_path)


def test_lee_fixtures(canal):
    media = canal.download_media("fixture://nota.ogg")
    assert media.content == b"OggS-audio" and media.mime_type == "audio/ogg"


@pytest.mark.parametrize("ref", ["fixture://../secreto.txt", "fixture://no-existe.ogg",
                                 "internal://media/x", "https://x.co/a.ogg"])
def test_no_lee_fuera_de_la_carpeta(canal, ref):
    with pytest.raises(MediaNotAvailableError):
        canal.download_media(ref)


def test_send_text_no_envia_y_guarda(canal):
    canal.send_text("100000000000001", "+573101234501", "Hola")
    assert canal.sent == [SentText("100000000000001", "+573101234501", "Hola")]


def test_entiende_el_formato_de_openbsp(canal):
    payload = {"entity": "messages", "action": "insert", "data": {
        "external_id": "wamid.1", "organization_address": "1", "sender_address": "573101234501",
        "conversation_address": "573101234501",
        "content": {"type": "text", "kind": "text", "text": "SÍ"}}}
    assert canal.parse_incoming(payload).text == "SÍ"


@pytest.mark.parametrize("valor,clase", [("fake", FakeChannel), ("openbsp", OpenBSPChannel)])
def test_canal_elegido_por_variable_de_entorno(monkeypatch, valor, clase):
    settings = Settings(
        whatsapp_channel=valor, openbsp_base_url="https://x.supabase.co",
        openbsp_publishable_key="p", openbsp_api_key="k", openbsp_organization_id="o",
    )
    monkeypatch.setattr(channels, "get_settings", lambda: settings)
    channels.get_channel.cache_clear()
    try:
        assert isinstance(channels.get_channel(), clase)
    finally:
        channels.get_channel.cache_clear()


def test_variable_de_entorno_invalida(monkeypatch):
    monkeypatch.setenv("WHATSAPP_CHANNEL", "twilio")
    with pytest.raises(ValueError):
        Settings()
