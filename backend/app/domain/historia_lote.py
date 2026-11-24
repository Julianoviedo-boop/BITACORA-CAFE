"""Historia del lote para el panel y para la página pública del lote.

- Panel (D-020, D-022): lista de lotes con su etapa y alertas abiertas, historia
  de cada lote con el estado de cada evento, mediciones en el tiempo y alertas.
- Página pública (D-022): solo eventos "confirmado" (regla de oro 2) y solo los
  datos que config/eventos.yaml marca como públicos. Nunca el texto original del
  caficultor ni datos de personas.

La etapa del lote no se guarda: sale de los eventos confirmados según
config/alertas.yaml (ver app/domain/alertas.py). Los nombres de los eventos, de
los datos y de las unidades salen de config/eventos.yaml y config/conversacion.yaml
(regla de oro 5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.db.models import Alert, Lot, LotEvent
from app.db.repositories import Repositories
from app.domain.alertas import ReglasAlertas, etapa_del_lote, get_reglas, nombre_etapa
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
    # Eventos confirmados.
    eventos: int
    ultimo_evento: dt.date | None
    ultimo_evento_texto: str | None
    # Actividad del último evento confirmado ("Volteo").
    ultima_actividad: str | None = None
    # Etapa en la que va el lote según config/alertas.yaml; None si no está en ninguna.
    etapa: str | None = None
    alertas_abiertas: int = 0


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
    # pendiente (esperando el SÍ), confirmado o rechazado.
    estado: str = "confirmado"


@dataclass(frozen=True)
class PuntoMedicion:
    momento: dt.datetime
    fecha_texto: str
    valor: float


@dataclass(frozen=True)
class SerieMedicion:
    campo: str
    etiqueta: str
    # Cómo se escribe la unidad ("°C", "%", "horas"); None si el dato no la trae.
    unidad: str | None
    puntos: list[PuntoMedicion]


@dataclass(frozen=True)
class AlertaVisible:
    id: uuid.UUID
    lote_id: uuid.UUID
    lote_codigo: str
    lote_nombre: str
    mensaje: str
    # "valor" (un dato fuera de rango) o "tiempo" (pasó el plazo sin un evento).
    clase: str
    # pendiente (todavía no se le mandó al productor) o enviada.
    estado: str
    desde: dt.datetime
    desde_texto: str


@dataclass(frozen=True)
class HistoriaLote:
    lote: ResumenLote
    eventos: list[EventoVisible]
    mediciones: list[SerieMedicion]
    alertas: list[AlertaVisible]


@dataclass(frozen=True)
class LotePublico:
    codigo: str
    nombre: str
    organizacion: str | None
    finca: str | None
    municipio: str | None
    departamento: str | None
    altitud_m: int | None
    variedad: str | None
    proceso: str | None


@dataclass(frozen=True)
class EventoPublico:
    actividad: str
    tipo: str
    fecha: dt.date
    fecha_texto: str
    datos: list[DatoVisible]


@dataclass(frozen=True)
class HistoriaPublica:
    lote: LotePublico
    eventos: list[EventoPublico]
    mediciones: list[SerieMedicion]


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------


def fecha_larga(fecha: dt.date) -> str:
    """La fecha como se lee en el panel: lunes 3 de noviembre de 2026."""
    return f"{_DIAS[fecha.weekday()]} {fecha.day} de {_MESES[fecha.month - 1]} de {fecha.year}"


def fecha_corta(fecha: dt.date) -> str:
    """Para la gráfica: 3 nov."""
    return f"{fecha.day} {_MESES[fecha.month - 1][:3]}"


def nombre_actividad(tipo: str, catalogo: CatalogoEventos) -> str:
    definicion = catalogo.tipos.get(tipo)
    return definicion.nombre if definicion else etiqueta_por_defecto(tipo)


def _campo_def(tipo: str, campo: str, catalogo: CatalogoEventos):
    definicion = catalogo.tipos.get(tipo)
    if definicion and campo in definicion.campos:
        return definicion.campos[campo]
    # El campo pudo salir del tipo después de guardado: se busca en los demás tipos.
    for otro in catalogo.tipos.values():
        if campo in otro.campos:
            return otro.campos[campo]
    return None


def _etiqueta(tipo: str, campo: str, catalogo: CatalogoEventos) -> str:
    definicion = _campo_def(tipo, campo, catalogo)
    return definicion.nombre_visible() if definicion else etiqueta_por_defecto(campo)


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


def _nombre_unidad(unidad: str | None, textos: TextosConversacion,
                   catalogo: CatalogoEventos) -> str | None:
    if not unidad:
        return None
    if unidad in textos.unidades:
        return textos.unidades[unidad][-1]
    return catalogo.unidades.get(unidad, unidad)


def _orden_campos(evento: LotEvent, catalogo: CatalogoEventos) -> list[str]:
    # En el orden del catálogo; lo que ya no esté en el catálogo, al final.
    definicion = catalogo.tipos.get(evento.event_type)
    orden = list(definicion.campos) if definicion else []
    data = evento.data or {}
    return orden + [c for c in data if c not in orden]


def _datos(evento: LotEvent, catalogo: CatalogoEventos, textos: TextosConversacion,
           *, solo_publicos: bool = False) -> list[DatoVisible]:
    data = evento.data or {}
    datos = []
    for campo in _orden_campos(evento, catalogo):
        if solo_publicos:
            definicion = _campo_def(evento.event_type, campo, catalogo)
            # Un dato que ya no está en el catálogo no se sabe si es público: no sale.
            if definicion is None or not definicion.publico:
                continue
        valor = _valor(data.get(campo), textos, catalogo)
        if valor is not None:
            datos.append(DatoVisible(etiqueta=_etiqueta(evento.event_type, campo, catalogo),
                                     valor=valor))
    return datos


def evento_visible(
    evento: LotEvent, catalogo: CatalogoEventos, textos: TextosConversacion
) -> EventoVisible:
    local = a_hora_colombia(evento.occurred_at)
    sin_hora = local.hour == 0 and local.minute == 0
    return EventoVisible(
        id=evento.id,
        tipo=evento.event_type,
        actividad=nombre_actividad(evento.event_type, catalogo),
        fecha=local.date(),
        fecha_texto=fecha_larga(local.date()),
        hora=None if sin_hora else formatear_hora(local.time()),
        datos=_datos(evento, catalogo, textos),
        texto_original=evento.original_text,
        estado=evento.status,
    )


def mediciones(
    eventos: Iterable[LotEvent], catalogo: CatalogoEventos, textos: TextosConversacion,
    *, solo_publicos: bool = False,
) -> list[SerieMedicion]:
    """Los datos numéricos con `grafica: true` en eventos.yaml, de eventos confirmados,
    una serie por dato y unidad (la temperatura de volteos y mediciones va junta)."""
    series: dict[tuple[str, str | None], list[PuntoMedicion]] = defaultdict(list)
    for evento in eventos:
        if evento.status != "confirmado":
            continue
        if solo_publicos and not _tipo_publico(evento.event_type, catalogo):
            continue
        for campo, crudo in (evento.data or {}).items():
            definicion = _campo_def(evento.event_type, campo, catalogo)
            if definicion is None or not definicion.grafica:
                continue
            if solo_publicos and not definicion.publico:
                continue
            if not isinstance(crudo, dict):
                continue
            valor = crudo.get("valor")
            if isinstance(valor, bool) or not isinstance(valor, int | float):
                continue
            local = a_hora_colombia(evento.occurred_at)
            series[(campo, crudo.get("unidad"))].append(PuntoMedicion(
                momento=local, fecha_texto=fecha_corta(local.date()), valor=float(valor)))
    resultado = []
    for (campo, unidad), puntos in series.items():
        puntos.sort(key=lambda p: p.momento)
        definicion = next((t.campos[campo] for t in catalogo.tipos.values()
                           if campo in t.campos), None)
        resultado.append(SerieMedicion(
            campo=campo,
            etiqueta=definicion.nombre_visible() if definicion else etiqueta_por_defecto(campo),
            unidad=_nombre_unidad(unidad, textos, catalogo),
            puntos=puntos,
        ))
    # En el orden en que aparecen los datos en eventos.yaml.
    orden = list(catalogo.campos)
    resultado.sort(key=lambda s: (orden.index(s.campo) if s.campo in orden else len(orden),
                                  s.unidad or ""))
    return resultado


def alerta_visible(alerta: Alert, lote: Lot) -> AlertaVisible:
    desde = alerta.due_at or alerta.created_at
    local = a_hora_colombia(desde)
    return AlertaVisible(
        id=alerta.id, lote_id=lote.id, lote_codigo=lote.code, lote_nombre=lote.name,
        mensaje=alerta.message,
        clase="tiempo" if alerta.rule_code.startswith("tiempo:") else "valor",
        estado=alerta.status, desde=desde,
        desde_texto=f"{fecha_larga(local.date())}, {formatear_hora(local.time())}",
    )


def _tipo_publico(tipo: str, catalogo: CatalogoEventos) -> bool:
    # Un tipo que ya no está en el catálogo no se sabe si es público: no sale.
    definicion = catalogo.tipos.get(tipo)
    return definicion is not None and definicion.publico


def _etapa(reglas: ReglasAlertas, eventos: Sequence[LotEvent]) -> str | None:
    etapa = etapa_del_lote(reglas, eventos)
    return nombre_etapa(reglas, etapa.clave) if etapa else None


def _resumen(lote: Lot, finca: str | None, confirmados: Sequence[LotEvent], alertas: int,
             catalogo: CatalogoEventos, reglas: ReglasAlertas) -> ResumenLote:
    ultimo = confirmados[-1] if confirmados else None
    fecha = a_hora_colombia(ultimo.occurred_at).date() if ultimo else None
    return ResumenLote(
        id=lote.id, codigo=lote.code, nombre=lote.name, finca=finca,
        variedad=lote.variety, proceso=lote.process, activo=lote.status == "activo",
        eventos=len(confirmados), ultimo_evento=fecha,
        ultimo_evento_texto=fecha_larga(fecha) if fecha else None,
        ultima_actividad=nombre_actividad(ultimo.event_type, catalogo) if ultimo else None,
        etapa=_etapa(reglas, confirmados), alertas_abiertas=alertas,
    )


# ---------------------------------------------------------------------------
# Casos de uso del panel (con organization_id, regla de oro 1)
# ---------------------------------------------------------------------------


def listar_lotes(
    repos: Repositories,
    organization_id: uuid.UUID,
    *,
    catalogo: CatalogoEventos | None = None,
    reglas: ReglasAlertas | None = None,
) -> list[ResumenLote]:
    catalogo = catalogo or catalogo_por_defecto()
    reglas = reglas or get_reglas()
    lotes = repos.lots.list_all(organization_id)
    fincas = {f.id: f.name for f in repos.farms.list(organization_id, limit=500)}
    por_lote: dict[uuid.UUID, list[LotEvent]] = defaultdict(list)
    for evento in repos.events.list_by_status(organization_id, "confirmado"):
        por_lote[evento.lot_id].append(evento)
    alertas: dict[uuid.UUID, int] = defaultdict(int)
    for alerta in repos.alerts.list_open(organization_id):
        alertas[alerta.lot_id] += 1
    return [
        _resumen(lote, fincas.get(lote.farm_id), por_lote.get(lote.id, []),
                 alertas.get(lote.id, 0), catalogo, reglas)
        for lote in lotes
    ]


def historia_lote(
    repos: Repositories,
    organization_id: uuid.UUID,
    lot_id: uuid.UUID,
    *,
    catalogo: CatalogoEventos | None = None,
    textos: TextosConversacion | None = None,
    reglas: ReglasAlertas | None = None,
) -> HistoriaLote | None:
    """La historia del lote, del evento más viejo al más nuevo, con los pendientes
    y rechazados marcados. None si el lote no existe en esta organización."""
    lote = repos.lots.get(organization_id, lot_id)
    if lote is None:
        return None
    catalogo = catalogo or catalogo_por_defecto()
    textos = textos or get_textos()
    reglas = reglas or get_reglas()
    eventos = repos.events.list_by_lot(organization_id, lot_id)
    confirmados = [e for e in eventos if e.status == "confirmado"]
    finca = repos.farms.get(organization_id, lote.farm_id)
    alertas = repos.alerts.list_open(organization_id, lot_id=lot_id)
    return HistoriaLote(
        lote=_resumen(lote, finca.name if finca else None, confirmados, len(alertas),
                      catalogo, reglas),
        eventos=[evento_visible(e, catalogo, textos) for e in eventos],
        mediciones=mediciones(confirmados, catalogo, textos),
        alertas=sorted((alerta_visible(a, lote) for a in alertas),
                       key=lambda a: a.desde, reverse=True),
    )


def alertas_abiertas(repos: Repositories, organization_id: uuid.UUID) -> list[AlertaVisible]:
    """Las alertas abiertas de la organización, la más reciente primero."""
    lotes = {lote.id: lote for lote in repos.lots.list_all(organization_id)}
    visibles = [alerta_visible(a, lotes[a.lot_id])
                for a in repos.alerts.list_open(organization_id) if a.lot_id in lotes]
    return sorted(visibles, key=lambda a: a.desde, reverse=True)


# ---------------------------------------------------------------------------
# Página pública (la ve un comprador, sin clave)
# ---------------------------------------------------------------------------


def historia_publica(
    repos: Repositories,
    organization_id: uuid.UUID,
    token: str,
    *,
    catalogo: CatalogoEventos | None = None,
    textos: TextosConversacion | None = None,
) -> HistoriaPublica | None:
    """Solo eventos confirmados de tipos públicos y sus datos públicos. Sin texto
    original, sin productores, sin horas. None si el token no es de un lote."""
    lote = repos.lots.get_by_public_token(organization_id, token)
    if lote is None:
        return None
    catalogo = catalogo or catalogo_por_defecto()
    textos = textos or get_textos()
    org = repos.organizations.get(organization_id)
    finca = repos.farms.get(organization_id, lote.farm_id)
    confirmados = repos.events.list_by_lot(organization_id, lote.id, status="confirmado")
    eventos = []
    for e in confirmados:
        if not _tipo_publico(e.event_type, catalogo):
            continue
        fecha = a_hora_colombia(e.occurred_at).date()
        eventos.append(EventoPublico(
            actividad=nombre_actividad(e.event_type, catalogo), tipo=e.event_type,
            fecha=fecha, fecha_texto=fecha_larga(fecha),
            datos=_datos(e, catalogo, textos, solo_publicos=True),
        ))
    return HistoriaPublica(
        lote=LotePublico(
            codigo=lote.code, nombre=lote.name, organizacion=org.name if org else None,
            finca=finca.name if finca else None,
            municipio=finca.municipality if finca else None,
            departamento=finca.department if finca else None,
            altitud_m=finca.altitude_m if finca else None,
            variedad=lote.variety, proceso=lote.process,
        ),
        eventos=eventos,
        mediciones=mediciones(confirmados, catalogo, textos, solo_publicos=True),
    )
