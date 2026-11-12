"""Repositorio base con filtro obligatorio por organization_id.

Reglas:
- Todo método público recibe `organization_id` como primer argumento.
- Toda lectura filtra por esa organización; toda escritura se hace sobre filas
  que primero se leyeron con ese filtro.
- `organization_id`, `id` y los timestamps no se pueden asignar ni cambiar desde
  los métodos genéricos (ni con add() ni con update()).
- Los repositorios no hacen commit: la transacción la controla quien los llama.
"""

import uuid
from collections.abc import Sequence
from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import Select, inspect, select
from sqlalchemy.orm import Session

from app.db.base import Base

M = TypeVar("M", bound=Base)


class OrganizationRequiredError(ValueError):
    """Se llamó un método del repositorio sin un organization_id válido."""


class ProtectedFieldError(ValueError):
    """Se intentó asignar un campo que el repositorio controla."""


def require_org(organization_id: Any) -> uuid.UUID:
    if not isinstance(organization_id, uuid.UUID):
        raise OrganizationRequiredError(
            f"organization_id debe ser un UUID, llegó {organization_id!r}"
        )
    return organization_id


class OrganizationScopedRepository(Generic[M]):
    model: ClassVar[type]
    # Campos que nunca se reciben en add()/update().
    protected_fields: ClassVar[frozenset[str]] = frozenset(
        {"id", "organization_id", "created_at", "updated_at"}
    )
    # Campos que se dan al crear pero no se pueden cambiar con update().
    immutable_fields: ClassVar[frozenset[str]] = frozenset()
    max_limit: ClassVar[int] = 500

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- helpers internos ---

    def _select(self, organization_id: uuid.UUID) -> Select[tuple[M]]:
        return select(self.model).where(self.model.organization_id == require_org(organization_id))

    def _check_fields(self, fields: dict[str, Any], *, updating: bool = False) -> None:
        forbidden = self.protected_fields | (self.immutable_fields if updating else frozenset())
        bad = forbidden & fields.keys()
        if bad:
            raise ProtectedFieldError(f"No se pueden asignar: {', '.join(sorted(bad))}")
        unknown = fields.keys() - inspect(self.model).column_attrs.keys()
        if unknown:
            raise AttributeError(
                f"{self.model.__name__} no tiene los campos: {', '.join(sorted(unknown))}"
            )

    def _limit(self, limit: int) -> int:
        return max(1, min(limit, self.max_limit))

    # --- API pública ---

    def get(self, organization_id: uuid.UUID, id: uuid.UUID) -> M | None:
        stmt = self._select(organization_id).where(self.model.id == id)
        return self.session.scalars(stmt).one_or_none()

    def list(
        self, organization_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> Sequence[M]:
        stmt = (
            self._select(organization_id)
            .order_by(self.model.created_at, self.model.id)
            .limit(self._limit(limit))
            .offset(max(0, offset))
        )
        return self.session.scalars(stmt).all()

    def add(self, organization_id: uuid.UUID, /, **fields: Any) -> M:
        require_org(organization_id)
        self._check_fields(fields)
        obj = self.model(organization_id=organization_id, **fields)
        self.session.add(obj)
        self.session.flush()
        return obj

    def update(self, organization_id: uuid.UUID, id: uuid.UUID, /, **fields: Any) -> M | None:
        self._check_fields(fields, updating=True)
        obj = self.get(organization_id, id)
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        self.session.flush()
        return obj

    def delete(self, organization_id: uuid.UUID, id: uuid.UUID) -> bool:
        obj = self.get(organization_id, id)
        if obj is None:
            return False
        self.session.delete(obj)
        self.session.flush()
        return True
