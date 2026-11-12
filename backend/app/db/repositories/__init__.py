"""Capa de repositorios: único punto de acceso a la base para endpoints y tareas.

Uso típico (en una tarea de Celery o un endpoint):

    with unit_of_work() as repos:
        lote = repos.lots.get_by_code(org_id, "ESP-01")
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.db.repositories.base import (
    OrganizationRequiredError,
    OrganizationScopedRepository,
    ProtectedFieldError,
)
from app.db.repositories.entities import (
    AlertRepository,
    FarmRepository,
    InboundMessageRepository,
    InvalidTransitionError,
    LotEventRepository,
    LotRepository,
    OrganizationRepository,
    ProducerRepository,
    ReportRepository,
)


class Repositories:
    """Agrupa todos los repositorios sobre una misma sesión (una transacción)."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.organizations = OrganizationRepository(session)
        self.farms = FarmRepository(session)
        self.producers = ProducerRepository(session)
        self.lots = LotRepository(session)
        self.messages = InboundMessageRepository(session)
        self.events = LotEventRepository(session)
        self.alerts = AlertRepository(session)
        self.reports = ReportRepository(session)


@contextmanager
def unit_of_work(session: Session | None = None) -> Iterator[Repositories]:
    """Abre una transacción: commit si todo sale bien, rollback si hay error."""
    if session is None:
        from app.db.session import get_sessionmaker

        session = get_sessionmaker()()
    try:
        yield Repositories(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "AlertRepository",
    "FarmRepository",
    "InboundMessageRepository",
    "InvalidTransitionError",
    "LotEventRepository",
    "LotRepository",
    "OrganizationRepository",
    "OrganizationRequiredError",
    "OrganizationScopedRepository",
    "ProducerRepository",
    "ProtectedFieldError",
    "ReportRepository",
    "Repositories",
    "unit_of_work",
]
