"""Endpoints de solo lectura para el panel web (ver docs/decisiones.md, D-020).

Los llama el servidor de Next.js, nunca el navegador. Se autentican con
`Authorization: Bearer <PANEL_API_TOKEN>`, y el token vale para una sola
organización: PANEL_ORGANIZATION_ID. Así, quien tenga el token no puede pedir
datos de otra organización cambiando un parámetro (regla de oro 1).
"""

import hmac
import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.db.repositories import Repositories, unit_of_work
from app.domain.historia_lote import historia_lote, listar_lotes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/panel", tags=["panel"])

MIN_TOKEN_LENGTH = 32

UnitOfWork = Callable[[], AbstractContextManager[Repositories]]


def get_unit_of_work() -> UnitOfWork:
    return unit_of_work


def _organizacion_configurada(settings: Settings) -> uuid.UUID | None:
    if len(settings.panel_api_token) < MIN_TOKEN_LENGTH:
        logger.error("PANEL_API_TOKEN vacío o con menos de %s caracteres", MIN_TOKEN_LENGTH)
        return None
    try:
        return uuid.UUID(settings.panel_organization_id)
    except ValueError:
        logger.error("PANEL_ORGANIZATION_ID no es un UUID válido")
        return None


def organizacion_del_panel(request: Request) -> uuid.UUID:
    """La organización que puede ver quien manda el token. 401 si el token no sirve."""
    settings = get_settings()
    org_id = _organizacion_configurada(settings)
    if org_id is None:
        raise HTTPException(status_code=503, detail="El panel no está configurado")
    auth = request.headers.get("authorization") or ""
    recibido = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    if not hmac.compare_digest(recibido.encode(), settings.panel_api_token.encode()):
        raise HTTPException(status_code=401, detail="No autorizado")
    return org_id


def repositorios(uow: UnitOfWork = Depends(get_unit_of_work)) -> Iterator[Repositories]:
    with uow() as repos:
        yield repos


@router.get("/lotes")
def lotes(
    org_id: uuid.UUID = Depends(organizacion_del_panel),
    repos: Repositories = Depends(repositorios),
) -> dict:
    return {"lotes": [asdict(lote) for lote in listar_lotes(repos, org_id)]}


@router.get("/lotes/{lot_id}")
def lote(
    lot_id: uuid.UUID,
    org_id: uuid.UUID = Depends(organizacion_del_panel),
    repos: Repositories = Depends(repositorios),
) -> dict:
    historia = historia_lote(repos, org_id, lot_id)
    if historia is None:
        raise HTTPException(status_code=404, detail="No encontramos ese lote")
    return asdict(historia)
