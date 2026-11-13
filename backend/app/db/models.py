"""Modelos SQLAlchemy.

Aislamiento entre organizaciones a nivel de base de datos:
cada tabla de negocio tiene UNIQUE (organization_id, id) y las referencias entre
tablas de negocio usan llaves foráneas compuestas (organization_id, <tabla>_id).
Así PostgreSQL rechaza, por ejemplo, un lote de la organización A colgado de una
finca de la organización B, aunque el código tenga un error.

No se definen `relationship()` a propósito: la carga perezosa de relaciones haría
consultas que no pasan por los repositorios ni por el filtro de organization_id.
"""

import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrganizationScoped, Timestamps, UUIDPrimaryKey

# Valores permitidos de los estados (máquinas de estado del sistema, no reglas
# agronómicas; esas viven en config/*.yaml).
CONSENT_STATUSES = ("pendiente", "aceptado", "revocado")
LOT_STATUSES = ("activo", "inactivo")
MESSAGE_TYPES = ("texto", "audio", "imagen", "otro")
# sin_consentimiento: llegó antes de que el productor aceptara el tratamiento de
# datos; no se procesa y se le borra el contenido.
PROCESSING_STATUSES = (
    "recibido", "transcrito", "extraido", "procesado", "sin_consentimiento", "error"
)
EVENT_STATUSES = ("pendiente", "confirmado", "rechazado")
# Alertas (app/domain/alertas.py):
#   pendiente  -> abierta, todavía no se le mandó al productor (solo en el panel).
#   enviada    -> abierta y ya se le mandó.
#   resuelta   -> se cerró sola (llegó el evento que faltaba, el valor volvió al rango...).
#   descartada -> la cerró alguien desde el panel.
ALERT_STATUSES = ("pendiente", "enviada", "resuelta", "descartada")
ALERT_OPEN_STATUSES = ("pendiente", "enviada")
REPORT_STATUSES = ("pendiente", "generado", "error")
# Conversación con un productor (app/domain/conversacion.py):
#   libre           -> no hay nada pendiente.
#   preguntando     -> se le preguntó un dato obligatorio que falta.
#   ofreciendo_lote -> nombró un lote que no existe y se le ofreció crearlo.
#   confirmando     -> se le mandó el resumen y se espera SÍ, corrección o CANCELAR.
CONVERSATION_STATES = ("libre", "preguntando", "ofreciendo_lote", "confirmando")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _same_org_fk(column: str, target: str) -> ForeignKeyConstraint:
    """FK compuesta (organization_id, column) -> target(organization_id, id)."""
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target}.organization_id", f"{target}.id"],
        ondelete="RESTRICT",
    )


def new_public_token() -> str:
    # 32 bytes aleatorios (256 bits) en base64url: imposible de adivinar o enumerar.
    return secrets.token_urlsafe(32)


class Organization(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    # Número de WhatsApp que recibe los mensajes de esta organización, tal como lo
    # identifica el canal: en OpenBSP es `organization_address`, el phone_number_id
    # de Meta (no el teléfono). Con él se sabe a qué organización va cada mensaje.
    whatsapp_address: Mapped[str | None] = mapped_column(String(64), unique=True)


class Farm(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    __tablename__ = "farms"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)

    name: Mapped[str] = mapped_column(String(200))
    municipality: Mapped[str | None] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(120))
    altitude_m: Mapped[int | None] = mapped_column(Integer)


class Producer(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    __tablename__ = "producers"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "phone"),
        _same_org_fk("farm_id", "farms"),
        CheckConstraint(_in("consent_status", CONSENT_STATUSES), name="consent_status"),
    )

    farm_id: Mapped[uuid.UUID | None]
    full_name: Mapped[str] = mapped_column(String(200))
    # Formato E.164, p. ej. +573001234567
    phone: Mapped[str] = mapped_column(String(20))
    consent_status: Mapped[str] = mapped_column(
        String(20), default="pendiente", server_default="pendiente"
    )
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Lot(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    __tablename__ = "lots"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "code"),
        _same_org_fk("farm_id", "farms"),
        CheckConstraint(_in("status", LOT_STATUSES), name="status"),
    )

    farm_id: Mapped[uuid.UUID]
    # Código legible para humanos, único dentro de la organización (p. ej. ESP-01).
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    variety: Mapped[str | None] = mapped_column(String(80))
    process: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="activo", server_default="activo")
    # Para el enlace público del reporte de trazabilidad.
    public_token: Mapped[str] = mapped_column(String(64), unique=True, default=new_public_token)


class InboundMessage(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        _same_org_fk("producer_id", "producers"),
        CheckConstraint(_in("message_type", MESSAGE_TYPES), name="message_type"),
        CheckConstraint(_in("processing_status", PROCESSING_STATUSES), name="processing_status"),
    )

    # Id del mensaje en WhatsApp. Único global: es la llave de idempotencia del webhook.
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    producer_id: Mapped[uuid.UUID | None]
    from_phone: Mapped[str] = mapped_column(String(20))
    message_type: Mapped[str] = mapped_column(String(10))
    text_body: Mapped[str | None] = mapped_column(Text)
    # Referencia del archivo en el canal (p. ej. internal://media/... en OpenBSP).
    media_ref: Mapped[str | None] = mapped_column(Text)
    transcription: Mapped[str | None] = mapped_column(Text)
    processing_status: Mapped[str] = mapped_column(
        String(20), default="recibido", server_default="recibido"
    )
    error_detail: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class LotEvent(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    __tablename__ = "lot_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        _same_org_fk("lot_id", "lots"),
        _same_org_fk("message_id", "inbound_messages"),
        CheckConstraint(_in("status", EVENT_STATUSES), name="status"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence"),
        # Historia del lote: eventos de un lote ordenados por fecha.
        Index("ix_lot_events_org_lot_occurred", "organization_id", "lot_id", "occurred_at"),
        # Búsquedas dentro del JSON (p. ej. data @> '{"insumo": "17-6-18"}').
        Index("ix_lot_events_data", "data", postgresql_using="gin",
              postgresql_ops={"data": "jsonb_path_ops"}),
    )

    # Null solo en tipos de evento que no son de un lote (requiere_lote: false en
    # config/eventos.yaml, p. ej. una venta).
    lot_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]
    # Tipo definido en config/eventos.yaml (abonada, fumigacion, recoleccion...).
    event_type: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    original_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(20), default="pendiente", server_default="pendiente")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alert(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        _same_org_fk("lot_id", "lots"),
        _same_org_fk("event_id", "lot_events"),
        CheckConstraint(_in("status", ALERT_STATUSES), name="status"),
        # Una alerta no se repite mientras siga abierta: lo garantiza la base, aunque
        # dos workers revisen el mismo lote a la vez.
        Index("uq_alerts_open_rule", "organization_id", "lot_id", "rule_code", unique=True,
              postgresql_where=text(_in("status", ALERT_OPEN_STATUSES))),
    )

    lot_id: Mapped[uuid.UUID]
    # Evento que la disparó (alertas por valor).
    event_id: Mapped[uuid.UUID | None]
    # Código de la regla en config/alertas.yaml (p. ej. "valor:secado:humedad").
    rule_code: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    # Desde cuándo vale la alerta: la hora del evento (por valor) o la hora en que
    # se venció el plazo (por tiempo).
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pendiente", server_default="pendiente")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Report(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        _same_org_fk("lot_id", "lots"),
        CheckConstraint(_in("status", REPORT_STATUSES), name="status"),
        CheckConstraint("date_from <= date_to", name="date_range"),
    )

    lot_id: Mapped[uuid.UUID]
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pendiente", server_default="pendiente")
    file_ref: Mapped[str | None] = mapped_column(String(512))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(UUIDPrimaryKey, Timestamps, OrganizationScoped, Base):
    """Estado de la conversación con un productor. Vive en la base (no en memoria)
    para que sobreviva a reinicios del worker; hay una sola fila por productor."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "producer_id"),
        _same_org_fk("producer_id", "producers"),
        _same_org_fk("source_message_id", "inbound_messages"),
        CheckConstraint(_in("state", CONVERSATION_STATES), name="state"),
        CheckConstraint("correction_rounds >= 0", name="correction_rounds"),
    )

    producer_id: Mapped[uuid.UUID]
    state: Mapped[str] = mapped_column(String(20), default="libre", server_default="libre")
    # Mensaje con el que empezó lo pendiente (los eventos quedan ligados a él).
    source_message_id: Mapped[uuid.UUID | None]
    # Eventos propuestos todavía sin confirmar (EventoPropuesto en JSON), en orden.
    drafts: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Ids de los lot_events "pendiente" creados para esos borradores (mismo orden).
    event_ids: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Pregunta abierta: {"indice", "campo", "tipo", "intentos", ...}.
    question: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    correction_rounds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Lo último que se le preguntó (para recordárselo después de un comando).
    last_prompt: Mapped[str | None] = mapped_column(Text)
    # Último mensaje del productor: abre la ventana de 24 horas de WhatsApp.
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Se pasó al asesor tras demasiadas correcciones o preguntas repetidas.
    advisor_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Respuesta que no se pudo mandar por estar fuera de la ventana de 24 horas.
    # PENDIENTE: mandarla con una plantilla aprobada (Channel.send_template ya existe;
    # ver D-017).
    unsent_reply: Mapped[str | None] = mapped_column(Text)
    unsent_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Sube en cada cambio: si dos workers atienden al mismo productor, el segundo
    # nota que el estado cambió mientras hablaba con Claude y vuelve a empezar.
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


BUSINESS_MODELS = (Farm, Producer, Lot, InboundMessage, LotEvent, Alert, Report, Conversation)
