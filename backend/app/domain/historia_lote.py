"""Historia del lote para el panel: lista de lotes y eventos confirmados en orden.

Solo se muestran eventos "confirmado" (regla de oro 2): lo pendiente o rechazado
no es parte de la historia. Los nombres de los eventos, de los datos y de las
unidades salen de config/eventos.yaml y config/conversacion.yaml (regla de oro 5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from app.db.models import LotEvent
from app.db.repositories import Repositories
from app.domain.conversacion import (
    _DIAS,
    _MESES,
    TextosConversacion,
    formatear_dato,
    formatear_hora,
    get_textos,
)
from app.extraction.extractor import DatoMedido
from app.extraction.prompts import a_hora_colombia
from app.extraction.schema import CatalogoEventos, catalogo_por_defecto, etiqueta_por_defecto


@dataclass(frozen=True)
class ResumenLote:
    id: uuid.UUID
    codigo: str
    nombre: str
    finca: str | None
    variedad: str | None
    proceso: str | None
    activo: bool
    eventos: int
    ultimo_evento: dt.date | None
    ultimo_evento_texto: str | None


@dataclass(frozen=True)
class DatoVisible:
    etiqueta: str
    valor: str


@dataclass(frozen=True)
class EventoVisible:
    id: uuid.UUID
    tipo: str
    actividad: str
    fecha: dt.date
    fecha_texto: str
    # None cuando el productor no dijo la hora (el evento quedó a las 00:00, D-016).
    hora: str | None
    datos: list[DatoVisible]
    texto_original: str | None


@dataclass(frozen=True)
class HistoriaLote:
    lote: ResumenLote
    eventos: list[EventoVisible]


def fecha_larga(fecha: dt.date) -> str:
    """La fecha como se lee en el panel: lunes 3 de noviembre de 2026."""
    return f"{_DIAS[fecha.weekday()]} {fecha.day} de {_MESES[fecha.month - 1]} de {fecha.year}"


def nombre_actividad(tipo: str, catalogo: CatalogoEventos) -> str:
    definicion = catalogo.tipos.get(tipo)
    return definicion.nombre if definicion else etiqueta_por_defecto(tipo)


def _etiqueta(tipo: str, campo: str, catalogo: CatalogoEventos) -> str:
    definicion = catalogo.tipos.get(tipo)
    if definicion and campo in definicion.campos:
        return definicion.campos[campo].nombre_visible()
    # El campo pudo salir del tipo después de guardado: se busca en los demás tipos.
    for otro in catalogo.tipos.values():
        if campo in otro.campos:
            return otro.campos[campo].nombre_visible()
    return etiqueta_por_defecto(campo)


def _valor(crudo: Any, textos: TextosConversacion, catalogo: CatalogoEventos) -> str | None:
    """lot_events.data guarda {campo: {valor, unidad}}; si algo viejo vino distinto,
    se muestra tal cual en lugar de romper la página."""
    if crudo is None:
        return None
    if isinstance(crudo, dict) and "valor" in crudo:
        if crudo["valor"] is None or crudo["valor"] == "":
            return None
        try:
            return formatear_dato(DatoMedido(**crudo), textos, catalogo)
        except (TypeError, ValueError):
            return str(crudo["valor"])
    return str(crudo)


def evento_visible(
    evento: LotEvent, catalogo: CatalogoEventos, textos: TextosConversacion
) -> EventoVisible:
    local = a_hora_colombia(evento.occurred_at)
    definicion = catalogo.tipos.get(evento.event_type)
    # En el orden del catálogo; lo que ya no esté en el catálogo, al final.
    orden = list(definicion.campos) if definicion else []
    data = evento.data or {}
    orden += [c for c in data if c not in orden]
    datos = [
        DatoVisible(etiqueta=_etiqueta(evento.event_type, campo, catalogo), valor=valor)
        for campo in orden
        if (valor := _valor(data.get(campo), textos, catalogo)) is not None
    ]
    sin_hora = local.hour == 0 and local.minute == 0
    return EventoVisible(
        id=evento.id,
        tipo=evento.event_type,
        actividad=nombre_actividad(evento.event_type, catalogo),
        fecha=local.date(),
        fecha_texto=fecha_larga(local.date()),
        hora=None if sin_hora else formatear_hora(local.time()),
        datos=datos,
        texto_original=evento.original_text,
    )


def listar_lotes(repos: Repositories, organization_id: uuid.UUID) -> list[ResumenLote]:
    lotes = repos.lots.list_all(organization_id)
    fincas = {f.id: f.name for f in repos.farms.list(organization_id, limit=500)}
    resumen = repos.events.summary_by_lot(organization_id)
    resultado = []
    for lote in lotes:
        n, ultimo = resumen.get(lote.id, (0, None))
        fecha = a_hora_colombia(ultimo).date() if ultimo else None
        resultado.append(ResumenLote(
            id=lote.id, codigo=lote.code, nombre=lote.name, finca=fincas.get(lote.farm_id),
            variedad=lote.variety, proceso=lote.process, activo=lote.status == "activo",
            eventos=n, ultimo_evento=fecha,
            ultimo_evento_texto=fecha_larga(fecha) if fecha else None,
        ))
    return resultado


def historia_lote(
    repos: Repositories,
    organization_id: uuid.UUID,
    lot_id: uuid.UUID,
    *,
    catalogo: CatalogoEventos | None = None,
    textos: TextosConversacion | None = None,
) -> HistoriaLote | None:
    """La historia del lote, del evento más viejo al más nuevo. None si el lote no
    existe en esta organización."""
    lote = repos.lots.get(organization_id, lot_id)
    if lote is None:
        return None
    catalogo = catalogo or catalogo_por_defecto()
    textos = textos or get_textos()
    eventos = repos.events.list_by_lot(organization_id, lot_id, status="confirmado")
    finca = repos.farms.get(organization_id, lote.farm_id)
    visibles = [evento_visible(e, catalogo, textos) for e in eventos]
    ultimo = visibles[-1].fecha if visibles else None
    return HistoriaLote(
        lote=ResumenLote(
            id=lote.id, codigo=lote.code, nombre=lote.name,
            finca=finca.name if finca else None, variedad=lote.variety, proceso=lote.process,
            activo=lote.status == "activo", eventos=len(visibles), ultimo_evento=ultimo,
            ultimo_evento_texto=fecha_larga(ultimo) if ultimo else None,
        ),
        eventos=visibles,
    )
