"""Qué va en el reporte de trazabilidad de un lote (D-023).

Sale de `historia_publica_de_lote`, la misma función de la página pública: lo que
ve el comprador en el PDF y en /l/<token> es lo mismo. Solo eventos confirmados;
un dato que falta se muestra como "No registrado", nunca se estima.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from app.db.models import Lot
from app.db.repositories import Repositories
from app.domain.conversacion import TextosConversacion, formatear_numero, get_textos
from app.domain.historia_lote import (
    NO_REGISTRADO,
    EventoPublico,
    SerieMedicion,
    fecha_larga,
    historia_publica_de_lote,
)
from app.extraction.prompts import a_hora_colombia
from app.extraction.schema import CatalogoEventos, catalogo_por_defecto


@dataclass(frozen=True)
class Fila:
    etiqueta: str
    valor: str


@dataclass(frozen=True)
class MedicionClave:
    dato: str
    mediciones: str
    minimo: str
    maximo: str
    ultima: str


@dataclass(frozen=True)
class DatosReporte:
    organizacion: str | None
    lote_nombre: str
    lote_codigo: str
    ficha: list[Fila]
    periodo: str
    generado: str
    eventos: list[EventoPublico]
    mediciones: list[MedicionClave]
    url_publica: str


def _o_no_registrado(valor: object | None) -> str:
    texto = str(valor).strip() if valor is not None else ""
    return texto or NO_REGISTRADO


def _con_unidad(valor: float, unidad: str | None) -> str:
    return f"{formatear_numero(valor)} {unidad}" if unidad else formatear_numero(valor)


def _mediciones_clave(series: list[SerieMedicion], catalogo: CatalogoEventos) -> list[MedicionClave]:
    """Una fila por cada medición pública de la gráfica (eventos.yaml: grafica y
    publico). Si el lote no tiene ninguna, la fila dice "No registrado"."""
    filas: list[MedicionClave] = []
    vistos: set[str] = set()
    for tipo in catalogo.tipos.values():
        if not tipo.publico:
            continue
        for campo in tipo.campos.values():
            if not (campo.grafica and campo.publico) or campo.clave in vistos:
                continue
            vistos.add(campo.clave)
            propias = [s for s in series if s.campo == campo.clave and s.puntos]
            if not propias:
                filas.append(MedicionClave(campo.nombre_visible(), NO_REGISTRADO,
                                           NO_REGISTRADO, NO_REGISTRADO, NO_REGISTRADO))
            for serie in propias:
                valores = [p.valor for p in serie.puntos]
                ultimo = serie.puntos[-1]
                filas.append(MedicionClave(
                    dato=serie.etiqueta,
                    mediciones=str(len(valores)),
                    minimo=_con_unidad(min(valores), serie.unidad),
                    maximo=_con_unidad(max(valores), serie.unidad),
                    ultima=f"{_con_unidad(ultimo.valor, serie.unidad)} ({ultimo.fecha_texto})",
                ))
    return filas


def _periodo(eventos: list[EventoPublico], desde: dt.date | None, hasta: dt.date | None) -> str:
    if desde or hasta:
        inicio = fecha_larga(desde) if desde else "el primer registro"
        fin = fecha_larga(hasta) if hasta else "hoy"
        return f"Del {inicio} al {fin}"
    if not eventos:
        return NO_REGISTRADO
    if eventos[0].fecha == eventos[-1].fecha:
        return f"El {eventos[0].fecha_texto}"
    return f"Del {eventos[0].fecha_texto} al {eventos[-1].fecha_texto}"


def url_publica(base: str, lote: Lot) -> str:
    return f"{base.rstrip('/')}/l/{lote.public_token}"


def datos_reporte(
    repos: Repositories,
    organization_id: uuid.UUID,
    lot_id: uuid.UUID,
    *,
    base_url: str,
    ahora: dt.datetime | None = None,
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
    catalogo: CatalogoEventos | None = None,
    textos: TextosConversacion | None = None,
) -> DatosReporte | None:
    """Los datos del reporte, o None si el lote no es de esta organización (regla
    de oro 1)."""
    lote = repos.lots.get(organization_id, lot_id)
    if lote is None:
        return None
    catalogo = catalogo or catalogo_por_defecto()
    textos = textos or get_textos()
    historia = historia_publica_de_lote(repos, organization_id, lote, catalogo=catalogo,
                                        textos=textos, desde=desde, hasta=hasta)
    l = historia.lote
    ubicacion = ", ".join(x for x in (l.municipio, l.departamento) if x)
    altitud = f"{formatear_numero(l.altitud_m)} m s. n. m." if l.altitud_m else None
    hoy = a_hora_colombia(ahora or dt.datetime.now(dt.UTC)).date()
    return DatosReporte(
        organizacion=l.organizacion,
        lote_nombre=l.nombre,
        lote_codigo=l.codigo,
        ficha=[
            Fila("Finca", _o_no_registrado(l.finca)),
            Fila("Ubicación", _o_no_registrado(ubicacion)),
            Fila("Altitud", _o_no_registrado(altitud)),
            Fila("Lote", f"{l.nombre} ({l.codigo})"),
            Fila("Variedad", _o_no_registrado(l.variedad)),
            Fila("Proceso", _o_no_registrado(l.proceso and l.proceso.capitalize())),
        ],
        periodo=_periodo(historia.eventos, desde, hasta),
        generado=fecha_larga(hoy),
        eventos=historia.eventos,
        mediciones=_mediciones_clave(historia.mediciones, catalogo),
        url_publica=url_publica(base_url, lote),
    )
