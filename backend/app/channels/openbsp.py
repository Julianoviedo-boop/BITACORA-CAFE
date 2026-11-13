"""Canal OpenBSP (https://github.com/matiasbattocchia/open-bsp-api).

Lo que se usa de OpenBSP y de dónde sale (detalle en docs/decisiones.md, D-013):

Entrada (webhook de la tabla `messages`, operación `insert`)
    POST {entity: "messages", action: "insert", data: <fila de messages>}.
    - data.sender_address es null en los mensajes propios (salientes)  -> se ignoran.
    - data.organization_address = phone_number_id del número que recibió.
    - data.conversation_address / sender_address = teléfono E.164 sin "+".
    - data.external_id = WAMID de Meta                                 -> idempotencia.
    - data.content: texto = {type: "text", kind: "text", text}.
      Archivo = {type: "file", kind: "audio"|"image"|..., file: {mime_type, uri,
      name?, size?}, text?: <pie de foto>}. Esta forma está documentada para
      ENVIAR; para mensajes ENTRANTES no hay ejemplo en la documentación: se tomó
      del código fuente (_shared/types/message_types.ts, whatsapp-webhook/index.ts).

Archivos entrantes
    MIGRATING_FROM_TWILIO.md dice que file.uri trae "una URL firmada de Storage",
    pero el webhook manda la fila tal cual (to_jsonb(new) en notify_webhook) y el
    código guarda `internal://media/organizations/<org>/attachments/<hash>`.
    Se soportan los dos casos:
      - internal://media/<llave>  -> GET {base}/storage/v1/object/authenticated/media/<llave>
        con los headers apikey + api-key. NO está documentado; se dedujo de la
        política RLS de storage.objects (lee el api-key) y del endpoint de subida
        que sí documentan. Verificar con un audio real.
      - https://<mismo host que OPENBSP_BASE_URL>/...  -> GET directo.
    Cualquier otra referencia se rechaza (evita que el webhook nos haga descargar
    de cualquier URL).

Salida
    POST {base}/rest/v1/messages con headers `apikey` y `api-key` (documentado).

Plantillas (sin verificar con un envío real)
    MIGRATING_FROM_TWILIO.md trae un ejemplo con `data.parameters` en la raíz, pero
    los tipos del código (_shared/types/whatsapp_template_types.ts) piden
    `language.policy: "deterministic"` y los parámetros dentro de
    `components: [{type: "body", parameters}]`, como la API de Meta. Se usa la forma
    de los tipos. Antes de configurar una plantilla en config/alertas.yaml hay que
    probarla con un número real (docs/decisiones.md, D-017).
"""

import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.channels.base import (
    Channel,
    ChannelError,
    ChannelTemporaryError,
    IncomingMessage,
    Media,
    MediaNotAvailableError,
    MessageKind,
)
from app.config import Settings

logger = logging.getLogger(__name__)

INTERNAL_MEDIA_PREFIX = "internal://media/"
# WhatsApp limita los audios a 16 MB; OpenBSP guarda hasta 50 MB.
MAX_MEDIA_BYTES = 20 * 1000 * 1000
HTTP_TIMEOUT_SECONDS = 15
# Tamaño de inbound_messages.external_id.
MAX_EXTERNAL_ID_LENGTH = 128


# ---------------------------------------------------------------------------
# Lectura del webhook (función pura; la reutiliza el canal falso)
# ---------------------------------------------------------------------------


def to_e164(address: Any) -> str | None:
    """'573001234567' -> '+573001234567'. None si no es un teléfono."""
    if not isinstance(address, str):
        return None
    digits = address.strip().removeprefix("+")
    if not (8 <= len(digits) <= 15 and digits.isdigit()):
        return None
    return f"+{digits}"


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_content(content: Any) -> tuple[MessageKind, str | None, str | None, str | None]:
    """Devuelve (tipo, texto, referencia del archivo, mime_type)."""
    if not isinstance(content, dict):
        return "otro", None, None, None
    ctype, ckind, text = content.get("type"), content.get("kind"), _str(content.get("text"))
    if ctype == "text":
        # Las reacciones (kind "reaction") y otras variantes no son texto del productor.
        return ("texto", text, None, None) if ckind == "text" else ("otro", None, None, None)
    if ctype == "file":
        file = content.get("file") if isinstance(content.get("file"), dict) else {}
        kind: MessageKind = {"audio": "audio", "image": "imagen"}.get(ckind, "otro")
        return kind, text, _str(file.get("uri")), _str(file.get("mime_type"))
    # Ubicaciones, contactos, plantillas, media_placeholder (archivo que OpenBSP no
    # pudo guardar), etc.
    return "otro", text, None, None


def parse_openbsp_payload(payload: Any) -> IncomingMessage | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("entity") != "messages" or payload.get("action") != "insert":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    if data.get("service", "whatsapp") != "whatsapp":
        return None

    sender = data.get("sender_address")
    if sender is None:
        return None  # mensaje propio (saliente)
    conversation = data.get("conversation_address")
    if conversation is not None and conversation != sender:
        return None  # mensaje en un grupo: no se atiende

    phone = to_e164(sender)
    channel_address = _str(data.get("organization_address"))
    external_id = _str(data.get("external_id"))
    if phone is None or channel_address is None or external_id is None:
        logger.warning("Mensaje de OpenBSP sin teléfono, número receptor o external_id; se ignora")
        return None
    if len(external_id) > MAX_EXTERNAL_ID_LENGTH:
        logger.warning("external_id demasiado largo (%d); se ignora", len(external_id))
        return None

    kind, text, media_ref, mime = _parse_content(data.get("content"))
    return IncomingMessage(
        external_id=external_id,
        channel_address=channel_address,
        sender_phone=phone,
        kind=kind,
        text=text,
        media_ref=media_ref,
        media_mime_type=mime,
    )


# ---------------------------------------------------------------------------
# Canal
# ---------------------------------------------------------------------------


class OpenBSPChannel(Channel):
    def __init__(
        self,
        *,
        base_url: str,
        publishable_key: str,
        api_key: str,
        organization_id: str,
        client: httpx.Client | None = None,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("OPENBSP_BASE_URL", base_url),
                ("OPENBSP_PUBLISHABLE_KEY", publishable_key),
                ("OPENBSP_API_KEY", api_key),
                ("OPENBSP_ORGANIZATION_ID", organization_id),
            )
            if not value
        ]
        if missing:
            raise ChannelError(f"Faltan variables de entorno de OpenBSP: {', '.join(missing)}")
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.netloc:
            raise ChannelError("OPENBSP_BASE_URL debe ser una URL https://")
        self._base = base_url.rstrip("/")
        self._host = parts.netloc
        self._organization_id = organization_id
        self._headers = {"apikey": publishable_key, "api-key": api_key}
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenBSPChannel":
        return cls(
            base_url=settings.openbsp_base_url,
            publishable_key=settings.openbsp_publishable_key,
            api_key=settings.openbsp_api_key,
            organization_id=settings.openbsp_organization_id,
        )

    def parse_incoming(self, payload: Any) -> IncomingMessage | None:
        return parse_openbsp_payload(payload)

    def send_text(self, channel_address: str, to_phone: str, text: str) -> None:
        self._insert_message(channel_address, to_phone,
                             {"version": "1", "type": "text", "kind": "text", "text": text})

    def send_template(
        self, channel_address: str, to_phone: str, template: str, language: str,
        params: Sequence[str],
    ) -> None:
        data: dict[str, Any] = {
            "name": template,
            "language": {"code": language, "policy": "deterministic"},
        }
        if params:
            # Meta rechaza variables con saltos de línea, tabs o muchos espacios seguidos.
            data["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": " ".join(p.split())} for p in params],
            }]
        self._insert_message(channel_address, to_phone,
                             {"version": "1", "type": "data", "kind": "template", "data": data})

    def _insert_message(self, channel_address: str, to_phone: str,
                        content: dict[str, Any]) -> None:
        body = {
            "organization_id": self._organization_id,
            "organization_address": channel_address,
            "conversation_address": to_phone.removeprefix("+"),
            "service": "whatsapp",
            "content": content,
        }
        try:
            resp = self._client.post(
                f"{self._base}/rest/v1/messages",
                json=body,
                headers={**self._headers, "Prefer": "return=minimal"},
            )
        except httpx.HTTPError as exc:
            raise ChannelTemporaryError(f"No se pudo conectar con OpenBSP: {exc}") from exc
        if resp.status_code >= 500:
            raise ChannelTemporaryError(f"OpenBSP respondió {resp.status_code}")
        if resp.status_code >= 400:
            raise ChannelError(f"OpenBSP rechazó el envío ({resp.status_code}): {resp.text[:300]}")

    def _media_url(self, media_ref: str) -> str:
        if media_ref.startswith(INTERNAL_MEDIA_PREFIX):
            key = media_ref.removeprefix(INTERNAL_MEDIA_PREFIX)
            if not key or any(part in ("", ".", "..") for part in key.split("/")):
                raise MediaNotAvailableError("Referencia de archivo inválida")
            return f"{self._base}/storage/v1/object/authenticated/media/{quote(key, safe='/')}"
        parts = urlsplit(media_ref)
        if parts.scheme == "https" and parts.netloc == self._host:
            return media_ref
        raise MediaNotAvailableError("Referencia de archivo no soportada")

    def download_media(self, media_ref: str) -> Media:
        url = self._media_url(media_ref)
        try:
            with self._client.stream("GET", url, headers=self._headers) as resp:
                if resp.status_code >= 500:
                    raise ChannelTemporaryError(f"OpenBSP respondió {resp.status_code}")
                if resp.status_code >= 400:
                    raise MediaNotAvailableError(f"OpenBSP respondió {resp.status_code}")
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_MEDIA_BYTES:
                        raise MediaNotAvailableError("El archivo es demasiado grande")
                    chunks.append(chunk)
                return Media(b"".join(chunks), resp.headers.get("content-type"))
        except httpx.HTTPError as exc:
            raise ChannelTemporaryError(f"No se pudo descargar el archivo: {exc}") from exc
