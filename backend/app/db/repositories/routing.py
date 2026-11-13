"""Enrutamiento de mensajes entrantes: ¿de qué organización es este número?

EXCEPCIÓN EXPLÍCITA a la regla de oro 1. Cuando llega un mensaje todavía no se
sabe la organización: hay que deducirla del número que lo recibió. Por eso esta
consulta no filtra por organization_id y no está en `Repositories` (cuyos métodos
exigen organization_id primero).

Para no abrir una puerta trasera:
- solo devuelve el id de la organización, nunca filas de negocio;
- busca por igualdad exacta sobre una columna UNIQUE (un número = una organización);
- todo lo que sigue después usa los repositorios con ese organization_id.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Organization


def find_organization_id_by_whatsapp_address(session: Session, address: str) -> uuid.UUID | None:
    if not address:
        return None
    stmt = select(Organization.id).where(Organization.whatsapp_address == address)
    return session.scalars(stmt).one_or_none()
