"""Caso de uso: de un texto del productor a eventos propuestos (estado pendiente).

`revisar` hace lo mismo cuando el productor responde una pregunta o corrige
eventos ya propuestos: devuelve la lista completa corregida.

    lotes = lotes_activos(repos, organization_id)
    resultado = extraer(texto, recibido_en=mensaje.received_at, lotes=lotes)

La salida de Claude no se usa tal cual: `normalizar` la revisa contra el
catálogo y contra los lotes de la organización, y corrige lo que no cuadra
(dejando una advertencia):

- codigo_lote que no es de un lote activo de ESA organización -> null.
- fecha en el futuro respecto al mensaje -> null (casi siempre es un plan).
- campos que no son del tipo de evento, o repetidos -> se quitan.
- números que vienen como texto -> se convierten o se quitan.
- unidad no permitida para el campo -> null.
- `faltantes` se recalcula: obligatorios del tipo (lote, fecha y campos) sin valor.

Nada de esto confirma eventos: el resultado se le muestra al productor para que
diga SÍ o NO (regla de oro 2).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from app.db.repositories import Repositories
from app.extraction import prompts
from app.extraction.llm import ClienteLLM
from app.extraction.prompts import LoteContexto
from app.extraction.schema import (
    FALTANTE_FECHA,
    FALTANTE_LOTE,
    CatalogoEventos,
    catalogo_por_defecto,
    construir_modelos,
)


class DatoMedido(BaseModel):
    valor: float | int | str
    unidad: str | None = None


class EventoPropuesto(BaseModel):
    tipo_evento: str
    codigo_lote: str | None
    lote_mencionado: str | None
    fecha: dt.date | None
    hora: dt.time | None
    # Todos los campos del tipo de evento; null = el productor no lo dijo.
    datos: dict[str, DatoMedido | None]
    faltantes: list[str]
    fragmento: str
    advertencias: list[str] = Field(default_factory=list)


class ResultadoExtraccion(BaseModel):
    eventos: list[EventoPropuesto]
    uso: dict[str, Any]


def lotes_activos(repos: Repositories, organization_id: uuid.UUID) -> list[LoteContexto]:
    """Lotes activos de UNA organización, con el nombre de su finca."""
    fincas = {f.id: f.name for f in repos.farms.list(organization_id, limit=500)}
    return [
        LoteContexto(codigo=l.code, nombre=l.name, variedad=l.variety,
                     finca=fincas.get(l.farm_id))
        for l in repos.lots.list_active(organization_id)
    ]


def _como_numero(valor: Any) -> float | int | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
    else:
        try:
            numero = float(str(valor).strip().replace(",", "."))
        except ValueError:
            return None
    return int(numero) if numero.is_integer() else numero


def _texto(valor: Any) -> str | None:
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = str(valor).strip()
    return texto or None


def normalizar(salida: BaseModel, catalogo: CatalogoEventos, lotes: Sequence[LoteContexto],
               hoy: dt.date) -> list[EventoPropuesto]:
    codigos = {l.codigo.casefold(): l.codigo for l in lotes}
    eventos: list[EventoPropuesto] = []
    for ev in salida.eventos:  # type: ignore[attr-defined]
        tipo = catalogo.tipos[ev.tipo_evento]
        avisos: list[str] = []

        codigo = None
        if ev.codigo_lote:
            codigo = codigos.get(ev.codigo_lote.strip().casefold())
            if codigo is None:
                avisos.append(f"codigo_lote {ev.codigo_lote!r} no es un lote activo; se quitó")

        fecha = ev.fecha
        if fecha is not None and fecha > hoy:
            avisos.append(f"fecha {fecha.isoformat()} es posterior al mensaje; se quitó")
            fecha = None

        datos: dict[str, DatoMedido | None] = {c: None for c in tipo.campos}
        for v in ev.valores:
            campo = tipo.campos.get(v.campo)
            if campo is None:
                avisos.append(f"el campo {v.campo!r} no es de {tipo.clave}; se quitó")
                continue
            if datos[campo.clave] is not None:
                avisos.append(f"el campo {campo.clave!r} vino repetido; se dejó el primero")
                continue
            if campo.tipo == "numero":
                numero = _como_numero(v.valor)
                if numero is None:
                    avisos.append(f"{campo.clave}: {v.valor!r} no es un número; se quitó")
                    continue
                unidad = v.unidad
                if unidad is not None and unidad not in campo.unidades:
                    avisos.append(f"{campo.clave}: la unidad {unidad!r} no es válida; se quitó")
                    unidad = None
                datos[campo.clave] = DatoMedido(valor=numero, unidad=unidad)
            else:
                texto = _texto(v.valor)
                if texto is not None:
                    datos[campo.clave] = DatoMedido(valor=texto, unidad=None)

        presentes = {FALTANTE_LOTE: codigo is not None, FALTANTE_FECHA: fecha is not None}
        presentes |= {c: d is not None for c, d in datos.items()}
        faltantes = [o for o in tipo.obligatorios() if not presentes[o]]

        eventos.append(EventoPropuesto(
            tipo_evento=tipo.clave, codigo_lote=codigo,
            lote_mencionado=(ev.lote_mencionado or "").strip() or None,
            fecha=fecha, hora=ev.hora, datos=datos, faltantes=faltantes,
            fragmento=ev.fragmento.strip(), advertencias=avisos,
        ))
    return eventos


def extraer(
    texto: str,
    *,
    recibido_en: dt.datetime,
    lotes: Sequence[LoteContexto],
    cliente: ClienteLLM | None = None,
    catalogo: CatalogoEventos | None = None,
) -> ResultadoExtraccion:
    """Extrae los eventos de un mensaje. `lotes` deben ser de la organización del mensaje."""
    return _llamar(prompts.construir_mensaje(texto, recibido_en, lotes),
                   recibido_en=recibido_en, lotes=lotes, cliente=cliente, catalogo=catalogo,
                   etiqueta="extraccion")


def revisar(
    eventos: Sequence[EventoPropuesto],
    texto: str,
    *,
    recibido_en: dt.datetime,
    lotes: Sequence[LoteContexto],
    pregunta: str | None = None,
    cliente: ClienteLLM | None = None,
    catalogo: CatalogoEventos | None = None,
) -> ResultadoExtraccion:
    """Aplica una respuesta o corrección del productor a eventos ya propuestos.

    Devuelve la lista completa corregida, normalizada igual que en `extraer`.
    `recibido_en` es la hora del mensaje NUEVO; las fechas ya propuestas van como
    fechas exactas, así que no se corren si pasó un día.
    """
    mensaje = prompts.construir_mensaje_revision(
        [a_salida(e) for e in eventos], texto, recibido_en, lotes, pregunta)
    return _llamar(mensaje, recibido_en=recibido_en, lotes=lotes, cliente=cliente,
                   catalogo=catalogo, etiqueta="revision")


def a_salida(evento: EventoPropuesto) -> dict[str, Any]:
    """Un evento ya normalizado, en el formato de la salida estructurada (para
    devolvérselo a Claude cuando el productor lo completa o corrige)."""
    return {
        "tipo_evento": evento.tipo_evento,
        "fragmento": evento.fragmento,
        "lote_mencionado": evento.lote_mencionado,
        "codigo_lote": evento.codigo_lote,
        "fecha": evento.fecha.isoformat() if evento.fecha else None,
        "hora": evento.hora.isoformat() if evento.hora else None,
        "valores": [{"campo": c, "valor": d.valor, "unidad": d.unidad}
                    for c, d in evento.datos.items() if d is not None],
        "faltantes": list(evento.faltantes),
    }


def _llamar(mensaje: str, *, recibido_en: dt.datetime, lotes: Sequence[LoteContexto],
            cliente: ClienteLLM | None, catalogo: CatalogoEventos | None,
            etiqueta: str) -> ResultadoExtraccion:
    catalogo = catalogo or catalogo_por_defecto()
    modelos = construir_modelos(catalogo)
    cliente = cliente or ClienteLLM()
    salida, uso = cliente.parse(
        system=prompts.construir_system(catalogo),
        mensaje=mensaje,
        output_format=modelos.Extraccion,
        etiqueta=etiqueta,
    )
    hoy = prompts.a_hora_colombia(recibido_en).date()
    return ResultadoExtraccion(eventos=normalizar(salida, catalogo, lotes, hoy),
                               uso=uso.a_dict())
