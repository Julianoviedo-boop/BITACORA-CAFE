"""Mandar el reporte en PDF por WhatsApp a quien lo pidió ("reporte lote 3", D-023).

Corre en el worker, después de que la conversación ya le respondió "Ya te mando el
reporte". Habla con WhatsApp solo a través de la interfaz Channel (regla de oro 4).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from app.channels import Channel, dentro_de_ventana
from app.db.repositories import Repositories
from app.domain.conversacion import TextosConversacion
from app.reports.datos import datos_reporte
from app.reports.pdf import generar_pdf, nombre_archivo

logger = logging.getLogger(__name__)


def enviar_reporte(
    repos: Repositories,
    channel: Channel,
    textos: TextosConversacion,
    organization_id: uuid.UUID,
    lot_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    base_url: str,
    ahora: dt.datetime,
) -> str:
    """Arma el PDF del lote y se lo manda como documento al productor del mensaje.

    Devuelve "enviado", "omitido" (el mensaje, el productor o el lote ya no están) o
    "fuera_de_ventana" (pasaron más de 24 horas: WhatsApp no deja mandar archivos).
    """
    msg = repos.messages.get(organization_id, message_id)
    if msg is None or msg.producer_id is None:
        return "omitido"
    org = repos.organizations.get(organization_id)
    producer = repos.producers.get(organization_id, msg.producer_id)
    if org is None or producer is None or not org.whatsapp_address:
        return "omitido"
    if producer.consent_status != "aceptado":
        return "omitido"
    conv = repos.conversations.get_by_producer(organization_id, producer.id)
    ultimo = max(t for t in (conv.last_inbound_at if conv else None, msg.received_at) if t)
    if not dentro_de_ventana(ultimo, ahora):
        logger.warning("reporte msg=%s fuera de la ventana de 24 h: no se envía", message_id)
        return "fuera_de_ventana"

    datos = datos_reporte(repos, organization_id, lot_id, base_url=base_url, ahora=ahora)
    if datos is None:
        return "omitido"
    pdf = generar_pdf(datos)
    channel.send_document(
        org.whatsapp_address, producer.phone, pdf, nombre_archivo(datos), "application/pdf",
        caption=textos.t("reporte_listo", lote=f"{datos.lote_nombre} ({datos.lote_codigo})"),
    )
    logger.info("reporte msg=%s lote=%s enviado (%d bytes)", message_id, lot_id, len(pdf))
    return "enviado"


def avisar_falla(
    repos: Repositories,
    channel: Channel,
    textos: TextosConversacion,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    ahora: dt.datetime,
) -> bool:
    """Si el PDF no se pudo armar, se le dice que lo vuelva a pedir."""
    msg = repos.messages.get(organization_id, message_id)
    if msg is None or msg.producer_id is None:
        return False
    org = repos.organizations.get(organization_id)
    producer = repos.producers.get(organization_id, msg.producer_id)
    if org is None or producer is None or not org.whatsapp_address:
        return False
    if not dentro_de_ventana(msg.received_at, ahora):
        return False
    channel.send_text(org.whatsapp_address, producer.phone, textos.t("reporte_error"))
    return True
