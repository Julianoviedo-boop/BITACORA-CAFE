"""Registro de un mensaje entrante (lo que hace el webhook antes de encolar)."""

import uuid
from dataclasses import dataclass
from typing import Literal

from app.channels.base import IncomingMessage
from app.db.repositories import Repositories
from app.db.repositories.routing import find_organization_id_by_whatsapp_address

Resultado = Literal["guardado", "duplicado", "numero_desconocido", "remitente_no_registrado"]


@dataclass(frozen=True)
class Registro:
    resultado: Resultado
    organization_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None


def registrar_mensaje_entrante(repos: Repositories, incoming: IncomingMessage) -> Registro:
    """Identifica la organización por el número receptor y guarda el mensaje una sola vez.

    - Número receptor desconocido: no se guarda nada.
    - Teléfono que no es un productor registrado de esa organización: no se guarda
      nada (solo escriben teléfonos autorizados, ver docs/especificacion.md).
    - external_id repetido: no se guarda de nuevo ni se vuelve a encolar.
    """
    org_id = find_organization_id_by_whatsapp_address(repos.session, incoming.channel_address)
    if org_id is None:
        return Registro("numero_desconocido")

    producer = repos.producers.get_by_phone(org_id, incoming.sender_phone)
    if producer is None:
        return Registro("remitente_no_registrado", org_id)

    msg, created = repos.messages.add_if_new(
        org_id,
        incoming.external_id,
        producer_id=producer.id,
        from_phone=incoming.sender_phone,
        message_type=incoming.kind,
        text_body=incoming.text,
        media_ref=incoming.media_ref,
    )
    if not created or msg is None:
        return Registro("duplicado", org_id)
    return Registro("guardado", org_id, msg.id)
