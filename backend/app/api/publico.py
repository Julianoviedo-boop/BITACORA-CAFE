"""Página pública del lote: lo que ve un comprador al abrir el enlace o el QR.

Sin clave: el token del lote (256 bits aleatorios, `lots.public_token`) es la
llave. Solo devuelve eventos confirmados y los datos marcados como públicos en
config/eventos.yaml; nunca el texto original del caficultor ni datos de personas
(ver docs/decisiones.md, D-022).
"""

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from app.db.repositories import Repositories, unit_of_work
from app.db.repositories.routing import find_organization_id_by_public_token
from app.domain.historia_lote import historia_publica

router = APIRouter(prefix="/publico", tags=["publico"])

UnitOfWork = Callable[[], AbstractContextManager[Repositories]]

# Como lo genera new_public_token (base64url). Lo demás ni se busca en la base.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}")


def get_unit_of_work() -> UnitOfWork:
    return unit_of_work


@router.get("/lotes/{token}")
def lote_publico(token: str, uow: UnitOfWork = Depends(get_unit_of_work)) -> dict:
    no_existe = HTTPException(status_code=404, detail="No encontramos ese lote")
    if not _TOKEN.fullmatch(token):
        raise no_existe
    with uow() as repos:
        org_id = find_organization_id_by_public_token(repos.session, token)
        historia = historia_publica(repos, org_id, token) if org_id else None
        if historia is None:
            raise no_existe
        return asdict(historia)
