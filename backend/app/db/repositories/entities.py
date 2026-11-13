"""Repositorios concretos. Todos exigen organization_id en cada método."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    CONSENT_STATUSES,
    PROCESSING_STATUSES,
    Alert,
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


class ReportRepository(OrganizationScopedRepository[Report]):
    model = Report

    def list_by_lot(self, organization_id: uuid.UUID, lot_id: uuid.UUID) -> Sequence[Report]:
        stmt = (
            self._select(organization_id)
            .where(Report.lot_id == lot_id)
            .order_by(Report.created_at.desc(), Report.id)
        )
        return self.session.scalars(stmt).all()
