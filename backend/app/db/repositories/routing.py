"""Enrutamiento de mensajes entrantes: ¿de qué organización es este número?

EXCEPCIÓN EXPLÍCITA a la regla de oro 1. Cuando llega un mensaje todavía no se
sabe la organización: hay que deducirla del número que lo recibió. Por eso esta
consulta no filtra por organization_id y no está en `Repositories` (cuyos métodos
exigen organization_id primero).

Para no abrir una puerta trasera:
- solo devuelve el id de la organización, nunca filas de negocio;
- busca por igualdad exacta sobre una columna UNIQUE (un número = una organización);
- todo lo que sigue después usa los repositorios con ese organization_id.

`find_organization_id_by_public_token` es la tercera: la página pública del lote
(/l/<token>, la que abre un comprador con el QR) no tiene sesión ni organización.
El token es aleatorio de 256 bits y UNIQUE, así que vale como llave: igual que con
el número, solo se devuelve el id de la organización y el lote se busca después
con `repos.lots.get_by_public_token(org_id, token)`.

`list_organization_ids` es la otra excepción: las tareas programadas (Celery beat,
app/domain/alertas.py) recorren las organizaciones de a una. Solo devuelve ids, y
cada organización se procesa después con sus repositorios.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lot, Organization


def find_organization_id_by_whatsapp_address(session: Session, address: str) -> uuid.UUID | None:
    if not address:
        return None
    stmt = select(Organization.id).where(Organization.whatsapp_address == address)
    return session.scalars(stmt).one_or_none()


def list_organization_ids(session: Session) -> list[uuid.UUID]:
    return list(session.scalars(select(Organization.id).order_by(Organization.id)))


def find_organization_id_by_public_token(session: Session, token: str) -> uuid.UUID | None:
    if not token:
        return None
    stmt = select(Lot.organization_id).where(Lot.public_token == token)
    return session.scalars(stmt).one_or_none()
