"""GET /lotes/{id}/reporte.pdf: reporte de trazabilidad del lote (D-023).

Lo pide el servidor del panel con el mismo token que /panel (D-020): el token vale
para una sola organización y un lote de otra da 404 (regla de oro 1).
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.panel import organizacion_del_panel, repositorios
from app.config import get_settings
from app.db.repositories import Repositories
from app.reports.datos import datos_reporte
from app.reports.pdf import generar_pdf, nombre_archivo

router = APIRouter(tags=["reportes"])


@router.get("/lotes/{lot_id}/reporte.pdf")
def reporte_pdf(
    lot_id: uuid.UUID,
    desde: dt.date | None = Query(default=None, description="Primer día (AAAA-MM-DD), incluido"),
    hasta: dt.date | None = Query(default=None, description="Último día (AAAA-MM-DD), incluido"),
    org_id: uuid.UUID = Depends(organizacion_del_panel),
    repos: Repositories = Depends(repositorios),
) -> Response:
    if desde and hasta and desde > hasta:
        raise HTTPException(status_code=422, detail="La fecha inicial va después de la final")
    datos = datos_reporte(repos, org_id, lot_id, base_url=get_settings().public_web_url,
                          desde=desde, hasta=hasta)
    if datos is None:
        raise HTTPException(status_code=404, detail="No encontramos ese lote")
    return Response(
        content=generar_pdf(datos),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{nombre_archivo(datos)}"',
            # Trae datos de la finca: que no quede en cachés intermedias.
            "Cache-Control": "no-store",
        },
    )
