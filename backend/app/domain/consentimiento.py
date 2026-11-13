"""Autorización de tratamiento de datos: nada se procesa hasta que el productor diga SÍ.

Los textos y las respuestas válidas viven en config/consentimiento.yaml (regla de oro 5).
"""

import unicodedata
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

from app.channels.base import Channel
from app.config import get_settings
from app.db.repositories import Repositories

Respuesta = Literal["si", "no"]


@dataclass(frozen=True)
class TextosConsentimiento:
    solicitud: str
    aceptado: str
    rechazado: str
    respuestas_si: frozenset[str]
    respuestas_no: frozenset[str]

    def solicitud_para(self, organizacion: str) -> str:
        return self.solicitud.replace("{organizacion}", organizacion).strip()


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, sin signos ni emojis, espacios simples."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c)
    )
    solo_letras = "".join(c if c.isalnum() else " " for c in sin_tildes)
    return " ".join(solo_letras.split())


def cargar_textos(config_dir: str | Path) -> TextosConsentimiento:
    path = Path(config_dir) / "consentimiento.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    faltan = [
        k for k in ("solicitud", "aceptado", "rechazado", "respuestas_si", "respuestas_no")
        if not data.get(k)
    ]
    if faltan:
        raise ValueError(f"{path}: faltan las claves {', '.join(faltan)}")
    for clave in ("respuestas_si", "respuestas_no"):
        raras = [r for r in data[clave] if not isinstance(r, str)]
        if raras:
            # YAML lee  no / si / yes  sin comillas como booleanos.
            raise ValueError(f"{path}: {clave} debe tener textos entre comillas; llegó {raras}")
    si = frozenset(normalizar(r) for r in data["respuestas_si"]) - {""}
    no = frozenset(normalizar(r) for r in data["respuestas_no"]) - {""}
    if si & no:
        raise ValueError(f"{path}: respuestas en SÍ y en NO a la vez: {sorted(si & no)}")
    return TextosConsentimiento(
        solicitud=str(data["solicitud"]),
        aceptado=str(data["aceptado"]).strip(),
        rechazado=str(data["rechazado"]).strip(),
        respuestas_si=si,
        respuestas_no=no,
    )


@lru_cache
def get_textos() -> TextosConsentimiento:
    return cargar_textos(get_settings().config_dir)


def clasificar_respuesta(texto: str | None, textos: TextosConsentimiento) -> Respuesta | None:
    if not texto:
        return None
    t = normalizar(texto)
    if t in textos.respuestas_si:
        return "si"
    if t in textos.respuestas_no:
        return "no"
    return None


def atender_mensaje(
    repos: Repositories,
    channel: Channel,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    textos: TextosConsentimiento,
) -> str:
    """Primer paso del worker para cada mensaje guardado. Devuelve qué se hizo.

    Los cambios de estado usan expected="recibido": si dos workers toman el mismo
    mensaje, solo uno lo atiende.
    """
    msgs = repos.messages
    msg = msgs.get(organization_id, message_id)
    if msg is None or msg.processing_status != "recibido":
        return "omitido"

    producer = repos.producers.get(organization_id, msg.producer_id) if msg.producer_id else None
    if producer is None:
        msgs.set_processing_status(
            organization_id, msg.id, "error", error_detail="Mensaje sin productor",
            expected="recibido",
        )
        return "sin_productor"

    if producer.consent_status == "aceptado":
        # Aquí se conectarán la transcripción y la extracción. El mensaje sigue
        # en "recibido" para esa etapa.
        return "listo_para_procesar"

    org = repos.organizations.get(organization_id)
    if org is None or not org.whatsapp_address:
        msgs.set_processing_status(
            organization_id, msg.id, "error", error_detail="La organización no tiene número",
            expected="recibido",
        )
        return "sin_numero"

    # Antes del SÍ no se transcribe nada: la autorización solo vale por escrito.
    respuesta = clasificar_respuesta(msg.text_body, textos) if msg.message_type == "texto" else None

    if respuesta is not None:
        # El SÍ / NO se conserva como constancia de la autorización.
        if msgs.set_processing_status(organization_id, msg.id, "procesado",
                                      expected="recibido") is None:
            return "omitido"
        if respuesta == "si":
            repos.producers.record_consent(organization_id, producer.id, "aceptado")
            channel.send_text(org.whatsapp_address, producer.phone, textos.aceptado)
            return "consentimiento_aceptado"
        repos.producers.record_consent(organization_id, producer.id, "revocado")
        channel.send_text(org.whatsapp_address, producer.phone, textos.rechazado)
        return "consentimiento_rechazado"

    # Cualquier otro mensaje: no se procesa, se borra su contenido y se pide la autorización.
    if msgs.set_processing_status(organization_id, msg.id, "sin_consentimiento",
                                  expected="recibido") is None:
        return "omitido"
    msgs.update(organization_id, msg.id, text_body=None, media_ref=None)
    channel.send_text(org.whatsapp_address, producer.phone, textos.solicitud_para(org.name))
    return "consentimiento_solicitado"
