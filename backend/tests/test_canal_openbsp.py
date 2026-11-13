"""Canal OpenBSP: lectura del webhook, envío y descarga (sin red: httpx.MockTransport)."""

import json

import httpx
import pytest

from app.channels.base import ChannelError, ChannelTemporaryError, MediaNotAvailableError
from app.channels.openbsp import (
    MAX_MEDIA_BYTES,
    OpenBSPChannel,
    parse_openbsp_payload,
    to_e164,
)

BASE = "https://openbsp.ejemplo.co"


def _payload(content=None, **data):
    row = {
        "id": "9b2d-uuid",
        "organization_id": "org-openbsp",
        "external_id": "wamid.ABC",
        "service": "whatsapp",
        "organization_address": "100000000000001",
        "conversation_address": "573101234501",
        "sender_address": "573101234501",
        "content": content or {"version": "1", "type": "text", "kind": "text", "text": "Hola"},
        "timestamp": "2026-11-12T12:00:00Z",
    }
    row.update(data)
    return {"entity": "messages", "action": "insert", "data": row}


def _canal(handler) -> OpenBSPChannel:
    return OpenBSPChannel(
        base_url=BASE,
        publishable_key="pub-key",
        api_key="secret-key",
        organization_id="org-openbsp",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


# --- parse_incoming ---


def test_texto():
    msg = parse_openbsp_payload(_payload())
    assert msg.external_id == "wamid.ABC"
    assert msg.channel_address == "100000000000001"
    assert msg.sender_phone == "+573101234501"
    assert (msg.kind, msg.text, msg.media_ref) == ("texto", "Hola", None)


def test_audio():
    content = {
        "version": "1", "type": "file", "kind": "audio",
        "file": {"mime_type": "audio/ogg; codecs=opus",
                 "uri": "internal://media/organizations/o/attachments/h", "size": 1234},
    }
    msg = parse_openbsp_payload(_payload(content))
    assert msg.kind == "audio"
    assert msg.media_ref == "internal://media/organizations/o/attachments/h"
    assert msg.media_mime_type == "audio/ogg; codecs=opus"
    assert msg.text is None


def test_imagen_con_pie_de_foto():
    content = {
        "version": "1", "type": "file", "kind": "image", "text": "Así quedó el lote",
        "file": {"mime_type": "image/jpeg", "uri": "internal://media/x"},
    }
    msg = parse_openbsp_payload(_payload(content))
    assert (msg.kind, msg.text, msg.media_ref) == ("imagen", "Así quedó el lote", "internal://media/x")


@pytest.mark.parametrize(
    "content",
    [
        {"type": "file", "kind": "video", "file": {"mime_type": "video/mp4", "uri": "internal://v"}},
        {"type": "data", "kind": "location", "data": {"latitude": 1, "longitude": 2}},
        {"type": "data", "kind": "media_placeholder", "data": {}},
        {"type": "text", "kind": "reaction", "text": "👍"},
        "no es un objeto",
    ],
    ids=["video", "ubicacion", "media_placeholder", "reaccion", "basura"],
)
def test_otros_tipos_quedan_como_otro(content):
    assert parse_openbsp_payload(_payload(content)).kind == "otro"


def test_mensaje_propio_se_ignora():
    assert parse_openbsp_payload(_payload(sender_address=None)) is None


@pytest.mark.parametrize(
    "payload",
    [
        {**_payload(), "action": "update"},  # cambio de estado
        {**_payload(), "entity": "conversations"},
        _payload(conversation_address="120363@g.us"),  # grupo
        _payload(service="instagram"),
        _payload(external_id=None),
        _payload(external_id="w" * 200),
        _payload(organization_address=""),
        _payload(sender_address="no-es-telefono"),
        {"entity": "messages", "action": "insert", "data": "x"},
        [],
        None,
    ],
    ids=["update", "otra_tabla", "grupo", "otro_servicio", "sin_external_id",
         "external_id_largo", "sin_numero_receptor", "telefono_invalido", "data_rara",
         "lista", "none"],
)
def test_avisos_que_no_son_mensajes_nuevos(payload):
    assert parse_openbsp_payload(payload) is None


@pytest.mark.parametrize(
    "entrada,esperado",
    [("573001234567", "+573001234567"), ("+573001234567", "+573001234567"),
     ("57300", None), ("abc", None), (None, None)],
)
def test_to_e164(entrada, esperado):
    assert to_e164(entrada) == esperado


# --- send_text ---


def test_send_text_inserta_en_messages_con_los_dos_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(201)

    _canal(handler).send_text("100000000000001", "+573101234501", "Hola don Jairo")

    assert seen["url"] == f"{BASE}/rest/v1/messages"
    assert seen["headers"]["apikey"] == "pub-key"
    assert seen["headers"]["api-key"] == "secret-key"
    assert "authorization" not in seen["headers"]
    assert seen["body"] == {
        "organization_id": "org-openbsp",
        "organization_address": "100000000000001",
        "conversation_address": "573101234501",
        "service": "whatsapp",
        "content": {"version": "1", "type": "text", "kind": "text", "text": "Hola don Jairo"},
    }


def test_send_text_error_5xx_es_temporal():
    canal = _canal(lambda r: httpx.Response(503))
    with pytest.raises(ChannelTemporaryError):
        canal.send_text("1", "+573101234501", "x")


def test_send_text_error_4xx_no_es_temporal():
    canal = _canal(lambda r: httpx.Response(400, text="bad content kind"))
    with pytest.raises(ChannelError) as exc:
        canal.send_text("1", "+573101234501", "x")
    assert not isinstance(exc.value, ChannelTemporaryError)


def test_send_text_sin_red_es_temporal():
    def handler(request):
        raise httpx.ConnectError("sin red")

    with pytest.raises(ChannelTemporaryError):
        _canal(handler).send_text("1", "+573101234501", "x")


def test_faltan_variables_de_entorno():
    with pytest.raises(ChannelError, match="OPENBSP_API_KEY"):
        OpenBSPChannel(base_url=BASE, publishable_key="p", api_key="", organization_id="o")


def test_base_url_debe_ser_https():
    with pytest.raises(ChannelError, match="https"):
        OpenBSPChannel(base_url="http://x.co", publishable_key="p", api_key="k", organization_id="o")


# --- download_media ---


def test_descarga_internal_por_storage_con_headers():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, content=b"OggS...", headers={"content-type": "audio/ogg"})

    media = _canal(handler).download_media("internal://media/organizations/o1/attachments/abc")

    assert seen["url"] == f"{BASE}/storage/v1/object/authenticated/media/organizations/o1/attachments/abc"
    assert seen["headers"]["apikey"] == "pub-key" and seen["headers"]["api-key"] == "secret-key"
    assert (media.content, media.mime_type) == (b"OggS...", "audio/ogg")


def test_descarga_url_firmada_del_mismo_host():
    url = f"{BASE}/storage/v1/object/sign/media/o/a/h?token=xyz"
    media = _canal(lambda r: httpx.Response(200, content=b"img")).download_media(url)
    assert media.content == b"img"


@pytest.mark.parametrize(
    "ref",
    [
        "https://otro-sitio.com/archivo.ogg",
        "http://openbsp.ejemplo.co/storage/v1/object/x",  # sin https
        "file:///etc/passwd",
        "internal://media/../../rest/v1/api_keys",
        "internal://media/",
        "123456789",  # media id de Meta sin descargar
    ],
)
def test_referencias_no_soportadas_no_hacen_peticion(ref):
    def handler(request):
        raise AssertionError(f"No debía pedir {request.url}")

    with pytest.raises(MediaNotAvailableError):
        _canal(handler).download_media(ref)


def test_descarga_404_no_disponible_y_5xx_temporal():
    with pytest.raises(MediaNotAvailableError):
        _canal(lambda r: httpx.Response(404)).download_media("internal://media/a/b")
    with pytest.raises(ChannelTemporaryError):
        _canal(lambda r: httpx.Response(502)).download_media("internal://media/a/b")


def test_descarga_rechaza_archivos_muy_grandes():
    grande = b"0" * (MAX_MEDIA_BYTES + 1)
    with pytest.raises(MediaNotAvailableError, match="grande"):
        _canal(lambda r: httpx.Response(200, content=grande)).download_media("internal://media/a/b")
