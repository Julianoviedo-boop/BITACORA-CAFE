"""Repositorios concretos. Todos exigen organization_id en cada método."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    ALERT_OPEN_STATUSES,
    CONSENT_STATUSES,
    PROCESSING_STATUSES,
    Alert,
    Conversation,
    Farm,
    InboundMessage,
    Lot,
    LotEvent,
    Organization,
    Producer,
    Report,
    new_public_token,
)
from app.db.repositories.base import (
    OrganizationScopedRepository,
    ProtectedFieldError,
    require_org,
)


class InvalidTransitionError(ValueError):
    """Cambio de estado no permitido (p. ej. confirmar un evento ya rechazado)."""


def _now() -> datetime:
    return datetime.now(UTC)


class OrganizationRepository:
    """La organización es la raíz: su propio id es el organization_id."""

    _editable: ClassVar[frozenset[str]] = frozenset({"name", "slug", "whatsapp_address"})

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, organization_id: uuid.UUID) -> Organization | None:
        stmt = select(Organization).where(Organization.id == require_org(organization_id))
        return self.session.scalars(stmt).one_or_none()

    def create(
        self,
        organization_id: uuid.UUID,
        *,
        name: str,
        slug: str,
        whatsapp_address: str | None = None,
    ) -> Organization:
        org = Organization(
            id=require_org(organization_id),
            name=name,
            slug=slug,
            whatsapp_address=whatsapp_address,
        )
        self.session.add(org)
        self.session.flush()
        return org

    def update(self, organization_id: uuid.UUID, /, **fields: Any) -> Organization | None:
        bad = fields.keys() - self._editable
        if bad:
            raise ProtectedFieldError(f"No se pueden asignar: {', '.join(sorted(bad))}")
        org = self.get(organization_id)
        if org is None:
            return None
        for key, value in fields.items():
            setattr(org, key, value)
        self.session.flush()
        return org


class FarmRepository(OrganizationScopedRepository[Farm]):
    model = Farm


class ProducerRepository(OrganizationScopedRepository[Producer]):
    model = Producer
    # El consentimiento solo cambia con record_consent (deja la fecha).
    protected_fields = OrganizationScopedRepository.protected_fields | {
        "consent_status",
        "consent_at",
    }

    def get_by_phone(self, organization_id: uuid.UUID, phone: str) -> Producer | None:
        stmt = self._select(organization_id).where(Producer.phone == phone)
        return self.session.scalars(stmt).one_or_none()

    def record_consent(
        self, organization_id: uuid.UUID, id: uuid.UUID, status: str
    ) -> Producer | None:
        if status not in CONSENT_STATUSES:
            raise ValueError(f"Estado de consentimiento inválido: {status!r}")
        producer = self.get(organization_id, id)
        if producer is None:
            return None
        producer.consent_status = status
        producer.consent_at = _now()
        self.session.flush()
        return producer

    def list_by_farm(
        self, organization_id: uuid.UUID, farm_id: uuid.UUID, *, consent_status: str | None = None
    ) -> Sequence[Producer]:
        stmt = self._select(organization_id).where(Producer.farm_id == farm_id)
        if consent_status is not None:
            stmt = stmt.where(Producer.consent_status == consent_status)
        return self.session.scalars(stmt.order_by(Producer.created_at, Producer.id)).all()


class LotRepository(OrganizationScopedRepository[Lot]):
    model = Lot
    # El token solo se cambia con regenerate_public_token.
    protected_fields = OrganizationScopedRepository.protected_fields | {"public_token"}

    def add(self, organization_id: uuid.UUID, /, **fields: Any) -> Lot:
        if not fields.get("code"):
            fields["code"] = self._next_code(organization_id)
        return super().add(organization_id, **fields)

    def _next_code(self, organization_id: uuid.UUID) -> str:
        # Consecutivo por organización; el UNIQUE (organization_id, code) protege
        # contra choques si dos procesos crean lotes al mismo tiempo.
        stmt = select(func.count()).select_from(Lot).where(
            Lot.organization_id == require_org(organization_id)
        )
        return f"LOTE-{self.session.scalar(stmt) + 1:03d}"

    def get_by_code(self, organization_id: uuid.UUID, code: str) -> Lot | None:
        stmt = self._select(organization_id).where(Lot.code == code)
        return self.session.scalars(stmt).one_or_none()

    def list_active(self, organization_id: uuid.UUID) -> Sequence[Lot]:
        stmt = (
            self._select(organization_id)
            .where(Lot.status == "activo")
            .order_by(Lot.code)
            .limit(self.max_limit)
        )
        return self.session.scalars(stmt).all()

    def list_all(self, organization_id: uuid.UUID) -> Sequence[Lot]:
        """Todos los lotes (activos e inactivos), primero los activos, por código."""
        stmt = (
            self._select(organization_id)
            .order_by(Lot.status, Lot.code)
            .limit(self.max_limit)
        )
        return self.session.scalars(stmt).all()

    def get_by_public_token(self, organization_id: uuid.UUID, token: str) -> Lot | None:
        stmt = self._select(organization_id).where(Lot.public_token == token)
        return self.session.scalars(stmt).one_or_none()

    def list_by_farm(self, organization_id: uuid.UUID, farm_id: uuid.UUID) -> Sequence[Lot]:
        stmt = self._select(organization_id).where(Lot.farm_id == farm_id).order_by(Lot.code)
        return self.session.scalars(stmt).all()

    def regenerate_public_token(self, organization_id: uuid.UUID, id: uuid.UUID) -> Lot | None:
        """Invalida el enlace público anterior (p. ej. si se compartió por error)."""
        lot = self.get(organization_id, id)
        if lot is None:
            return None
        lot.public_token = new_public_token()
        self.session.flush()
        return lot


class InboundMessageRepository(OrganizationScopedRepository[InboundMessage]):
    model = InboundMessage
    immutable_fields = frozenset({"external_id"})

    def get_by_external_id(
        self, organization_id: uuid.UUID, external_id: str
    ) -> InboundMessage | None:
        stmt = self._select(organization_id).where(InboundMessage.external_id == external_id)
        return self.session.scalars(stmt).one_or_none()

    def add_if_new(
        self, organization_id: uuid.UUID, external_id: str, /, **fields: Any
    ) -> tuple[InboundMessage | None, bool]:
        """Guarda el mensaje solo si su external_id no existe (idempotencia del webhook).

        Devuelve (mensaje, creado). Si ya existía se devuelve el existente con
        creado=False; si existía en otra organización se devuelve (None, False)
        sin revelar ni tocar ese mensaje.
        """
        require_org(organization_id)
        self._check_fields(fields)
        stmt = (
            insert(InboundMessage)
            .values(organization_id=organization_id, external_id=external_id, **fields)
            .on_conflict_do_nothing(index_elements=[InboundMessage.external_id])
            .returning(InboundMessage.id)
        )
        new_id = self.session.execute(stmt).scalar_one_or_none()
        if new_id is not None:
            return self.get(organization_id, new_id), True
        return self.get_by_external_id(organization_id, external_id), False

    def set_processing_status(
        self,
        organization_id: uuid.UUID,
        id: uuid.UUID,
        status: str,
        *,
        error_detail: str | None = None,
        expected: str | None = None,
    ) -> InboundMessage | None:
        """Cambia el estado de procesamiento.

        Con `expected`, el cambio es atómico y solo ocurre si el mensaje está en ese
        estado (devuelve None si no). Sirve para que dos workers no atiendan el
        mismo mensaje.
        """
        if status not in PROCESSING_STATUSES:
            raise ValueError(f"Estado de procesamiento inválido: {status!r}")
        if expected is not None and expected not in PROCESSING_STATUSES:
            raise ValueError(f"Estado de procesamiento inválido: {expected!r}")
        stmt = (
            update(InboundMessage)
            .where(
                InboundMessage.organization_id == require_org(organization_id),
                InboundMessage.id == id,
            )
            .values(processing_status=status, error_detail=error_detail)
            .returning(InboundMessage.id)
            .execution_options(synchronize_session="fetch")
        )
        if expected is not None:
            stmt = stmt.where(InboundMessage.processing_status == expected)
        if self.session.execute(stmt).scalar_one_or_none() is None:
            return None
        return self.get(organization_id, id)


class LotEventRepository(OrganizationScopedRepository[LotEvent]):
    model = LotEvent
    # Regla de oro 2: el estado solo cambia con confirm()/reject(), que el dominio
    # llama únicamente tras la respuesta explícita del usuario por WhatsApp.
    protected_fields = OrganizationScopedRepository.protected_fields | {
        "status",
        "confirmed_at",
    }

    def list_by_lot(
        self, organization_id: uuid.UUID, lot_id: uuid.UUID, *, status: str | None = None
    ) -> Sequence[LotEvent]:
        stmt = self._select(organization_id).where(LotEvent.lot_id == lot_id)
        if status is not None:
            stmt = stmt.where(LotEvent.status == status)
        stmt = stmt.order_by(LotEvent.occurred_at, LotEvent.id)
        return self.session.scalars(stmt).all()

    def list_by_status(self, organization_id: uuid.UUID, status: str) -> Sequence[LotEvent]:
        """Todos los eventos de lotes en ese estado, del más viejo al más nuevo.

        Para calcular la etapa de cada lote en la lista del panel con una sola
        consulta (en lugar de una por lote)."""
        stmt = (
            self._select(organization_id)
            .where(LotEvent.status == status, LotEvent.lot_id.is_not(None))
            .order_by(LotEvent.occurred_at, LotEvent.id)
        )
        return self.session.scalars(stmt).all()

    def summary_by_lot(
        self, organization_id: uuid.UUID, *, status: str = "confirmado"
    ) -> dict[uuid.UUID, tuple[int, datetime]]:
        """{lot_id: (cuántos eventos, fecha del último)} de los eventos en ese estado."""
        stmt = (
            select(LotEvent.lot_id, func.count(), func.max(LotEvent.occurred_at))
            .where(LotEvent.organization_id == require_org(organization_id),
                   LotEvent.status == status, LotEvent.lot_id.is_not(None))
            .group_by(LotEvent.lot_id)
        )
        return {lot_id: (n, ultimo) for lot_id, n, ultimo in self.session.execute(stmt)}

    def _transition(
        self, organization_id: uuid.UUID, id: uuid.UUID, new_status: str
    ) -> LotEvent | None:
        event = self.get(organization_id, id)
        if event is None:
            return None
        if event.status != "pendiente":
            raise InvalidTransitionError(
                f"El evento está {event.status!r}; solo se puede cambiar si está 'pendiente'"
            )
        event.status = new_status
        if new_status == "confirmado":
            event.confirmed_at = _now()
        self.session.flush()
        return event

    def confirm(self, organization_id: uuid.UUID, id: uuid.UUID) -> LotEvent | None:
        return self._transition(organization_id, id, "confirmado")

    def reject(self, organization_id: uuid.UUID, id: uuid.UUID) -> LotEvent | None:
        return self._transition(organization_id, id, "rechazado")


class AlertRepository(OrganizationScopedRepository[Alert]):
    model = Alert

    def list_pending(self, organization_id: uuid.UUID) -> Sequence[Alert]:
        stmt = (
            self._select(organization_id)
            .where(Alert.status == "pendiente")
            .order_by(Alert.due_at.nulls_last(), Alert.id)
        )
        return self.session.scalars(stmt).all()

    def mark_sent(self, organization_id: uuid.UUID, id: uuid.UUID) -> Alert | None:
        alert = self.get(organization_id, id)
        if alert is None:
            return None
        alert.status = "enviada"
        alert.sent_at = _now()
        self.session.flush()
        return alert

    def open_if_absent(
        self,
        organization_id: uuid.UUID,
        /,
        *,
        lot_id: uuid.UUID,
        rule_code: str,
        message: str,
        event_id: uuid.UUID | None = None,
        due_at: datetime | None = None,
    ) -> Alert | None:
        """Abre la alerta si no hay otra abierta de la misma regla para el lote.

        Devuelve la alerta nueva, o None si ya había una abierta. El índice único
        parcial uq_alerts_open_rule lo garantiza aunque dos workers lo intenten a
        la vez (ON CONFLICT DO NOTHING).
        """
        stmt = (
            insert(Alert)
            .values(organization_id=require_org(organization_id), lot_id=lot_id,
                    rule_code=rule_code, message=message, event_id=event_id, due_at=due_at)
            .on_conflict_do_nothing(
                index_elements=[Alert.organization_id, Alert.lot_id, Alert.rule_code],
                index_where=Alert.status.in_(ALERT_OPEN_STATUSES),
            )
            .returning(Alert.id)
        )
        new_id = self.session.execute(stmt).scalar_one_or_none()
        return self.get(organization_id, new_id) if new_id is not None else None

    def list_open(
        self, organization_id: uuid.UUID, *, lot_id: uuid.UUID | None = None
    ) -> Sequence[Alert]:
        stmt = self._select(organization_id).where(Alert.status.in_(ALERT_OPEN_STATUSES))
        if lot_id is not None:
            stmt = stmt.where(Alert.lot_id == lot_id)
        return self.session.scalars(stmt.order_by(Alert.created_at, Alert.id)).all()

    def resolve_open(
        self,
        organization_id: uuid.UUID,
        lot_id: uuid.UUID,
        rule_code: str,
        *,
        due_until: datetime | None = None,
    ) -> int:
        """Cierra ("resuelta") la alerta abierta de la regla en el lote.

        Con due_until, solo si la alerta vale desde antes de esa hora: una medición
        vieja que llega tarde no cierra una alerta más reciente. Devuelve cuántas cerró.
        """
        stmt = (
            update(Alert)
            .where(Alert.organization_id == require_org(organization_id),
                   Alert.lot_id == lot_id, Alert.rule_code == rule_code,
                   Alert.status.in_(ALERT_OPEN_STATUSES))
            .values(status="resuelta", resolved_at=_now(), updated_at=func.now())
            .execution_options(synchronize_session="fetch")
        )
        if due_until is not None:
            stmt = stmt.where(Alert.due_at <= due_until)
        return self.session.execute(stmt).rowcount


class ReportRepository(OrganizationScopedRepository[Report]):
    model = Report

    def list_by_lot(self, organization_id: uuid.UUID, lot_id: uuid.UUID) -> Sequence[Report]:
        stmt = (
            self._select(organization_id)
            .where(Report.lot_id == lot_id)
            .order_by(Report.created_at.desc(), Report.id)
        )
        return self.session.scalars(stmt).all()


class ConversationRepository(OrganizationScopedRepository[Conversation]):
    model = Conversation
    immutable_fields = frozenset({"producer_id"})

    def get_by_producer(
        self, organization_id: uuid.UUID, producer_id: uuid.UUID, *, for_update: bool = False
    ) -> Conversation | None:
        """La conversación del productor. Con for_update, bloquea la fila hasta el
        final de la transacción (dos workers no la cambian a la vez)."""
        stmt = self._select(organization_id).where(Conversation.producer_id == producer_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return self.session.scalars(stmt).one_or_none()

    def get_or_create(self, organization_id: uuid.UUID, producer_id: uuid.UUID) -> Conversation:
        """Devuelve la conversación del productor; la crea vacía si no existe.

        Un productor de otra organización no se puede usar: la FK compuesta
        (organization_id, producer_id) lo rechaza.
        """
        stmt = (
            insert(Conversation)
            .values(organization_id=require_org(organization_id), producer_id=producer_id)
            .on_conflict_do_nothing(
                index_elements=[Conversation.organization_id, Conversation.producer_id]
            )
        )
        self.session.execute(stmt)
        conv = self.get_by_producer(organization_id, producer_id)
        assert conv is not None
        return conv
