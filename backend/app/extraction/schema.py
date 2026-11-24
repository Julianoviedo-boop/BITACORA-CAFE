"""Modelos Pydantic de la extracción, generados desde config/eventos.yaml.

Hay dos capas:

1. El catálogo (CatalogoEventos): lo que dice eventos.yaml, ya validado.
2. Los modelos que se le pasan a Claude (construir_modelos): la "salida
   estructurada". Se arman con create_model a partir del catálogo, así que
   agregar un tipo o un campo en el YAML no exige tocar código.

Por qué los valores van en una lista y no un campo por cada dato
-----------------------------------------------------------------
La API compila el esquema a una gramática y pone límites: máximo 24 parámetros
opcionales y 16 con tipos unión (cada `X | None` cuenta) en todo el esquema. Un
modelo por tipo de evento con un campo anulable por dato se pasa de ese límite
con el catálogo actual y crecería con cada campo nuevo. Por eso cada evento trae
`valores: [{campo, valor, unidad}]`, con `campo` y `unidad` restringidos a los
nombres del YAML (enum). El esquema crece en valores del enum, no en uniones.

Lo que el productor no dijo NO se manda en `valores`; al normalizar
(app/extraction/extractor.py) queda en null y, si es obligatorio, va a
`faltantes`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, create_model

from app.config import get_settings

TipoDato = Literal["numero", "texto"]
# Faltantes comunes a todos los eventos (además de los campos obligatorios).
FALTANTE_LOTE = "lote"
FALTANTE_FECHA = "fecha"
_RESERVADOS = {FALTANTE_LOTE, FALTANTE_FECHA, "hora"}


class CatalogoInvalidoError(ValueError):
    """eventos.yaml tiene un error; el mensaje dice dónde."""


# ---------------------------------------------------------------------------
# Catálogo (lo que dice eventos.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CampoDef:
    clave: str
    tipo: TipoDato
    obligatorio: bool
    unidades: tuple[str, ...]
    descripcion: str
    # Lo que se le pregunta al productor si falta (solo se usa en los obligatorios).
    pregunta: str | None = None
    # Cómo se llama el dato en el panel ("Horas de fermentación").
    etiqueta: str | None = None

    def nombre_visible(self) -> str:
        return self.etiqueta or etiqueta_por_defecto(self.clave)


@dataclass(frozen=True)
class TipoEventoDef:
    clave: str
    nombre: str
    descripcion: str
    requiere_lote: bool
    campos: dict[str, CampoDef]

    def obligatorios(self) -> list[str]:
        """Lo que hay que preguntar si falta, en el orden en que se pregunta."""
        comunes = ([FALTANTE_LOTE] if self.requiere_lote else []) + [FALTANTE_FECHA]
        return comunes + [c.clave for c in self.campos.values() if c.obligatorio]


@dataclass(frozen=True)
class CatalogoEventos:
    unidades: dict[str, str]
    tipos: dict[str, TipoEventoDef]
    # Tipo de dato de cada campo, juntando todos los tipos de evento.
    campos: dict[str, TipoDato] = field(default_factory=dict)

    def faltantes_posibles(self) -> list[str]:
        return [FALTANTE_LOTE, FALTANTE_FECHA] + list(self.campos)


def etiqueta_por_defecto(clave: str) -> str:
    """Nombre del campo cuando eventos.yaml no trae etiqueta: cantidad_cereza -> Cantidad cereza."""
    texto = clave.replace("_", " ").strip()
    return texto[:1].upper() + texto[1:]


def _es_clave(valor: Any) -> bool:
    return isinstance(valor, str) and valor.isidentifier() and valor.isascii() and valor.islower()


def cargar_catalogo(config_dir: str | Path) -> CatalogoEventos:
    path = Path(config_dir) / "eventos.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    unidades = data.get("unidades") or {}
    if not isinstance(unidades, dict) or not unidades:
        raise CatalogoInvalidoError(f"{path}: falta la lista de unidades")
    for clave in unidades:
        if not _es_clave(clave):
            raise CatalogoInvalidoError(f"{path}: unidad con nombre inválido: {clave!r}")

    tipos_raw = data.get("tipos") or {}
    if not isinstance(tipos_raw, dict) or not tipos_raw:
        raise CatalogoInvalidoError(f"{path}: no hay tipos de evento")

    tipos: dict[str, TipoEventoDef] = {}
    campos_globales: dict[str, TipoDato] = {}
    for clave_tipo, t in tipos_raw.items():
        donde = f"{path}: tipos.{clave_tipo}"
        if not _es_clave(clave_tipo):
            raise CatalogoInvalidoError(f"{donde}: la clave debe ir en minúsculas, sin tildes")
        if not isinstance(t, dict) or not t.get("nombre") or not t.get("descripcion"):
            raise CatalogoInvalidoError(f"{donde}: necesita nombre y descripcion")
        campos: dict[str, CampoDef] = {}
        for clave_campo, c in (t.get("campos") or {}).items():
            donde_c = f"{donde}.campos.{clave_campo}"
            if not _es_clave(clave_campo) or clave_campo in _RESERVADOS:
                raise CatalogoInvalidoError(f"{donde_c}: nombre inválido o reservado")
            c = c or {}
            tipo = c.get("tipo")
            if tipo not in ("numero", "texto"):
                raise CatalogoInvalidoError(f"{donde_c}: tipo debe ser numero o texto")
            if campos_globales.setdefault(clave_campo, tipo) != tipo:
                raise CatalogoInvalidoError(
                    f"{donde_c}: el campo ya existe en otro tipo de evento como "
                    f"{campos_globales[clave_campo]}"
                )
            u = tuple(c.get("unidades") or ())
            if tipo == "texto" and u:
                raise CatalogoInvalidoError(f"{donde_c}: un campo de texto no lleva unidades")
            if tipo == "numero" and not u:
                raise CatalogoInvalidoError(f"{donde_c}: un campo numérico necesita unidades")
            desconocidas = [x for x in u if x not in unidades]
            if desconocidas:
                raise CatalogoInvalidoError(f"{donde_c}: unidades desconocidas {desconocidas}")
            campos[clave_campo] = CampoDef(
                clave=clave_campo, tipo=tipo, obligatorio=bool(c.get("obligatorio", False)),
                unidades=u, descripcion=str(c.get("descripcion", "")).strip(),
                pregunta=str(c.get("pregunta") or "").strip() or None,
                etiqueta=str(c.get("etiqueta") or "").strip() or None,
            )
        tipos[clave_tipo] = TipoEventoDef(
            clave=clave_tipo, nombre=str(t["nombre"]).strip(),
            descripcion=" ".join(str(t["descripcion"]).split()),
            requiere_lote=bool(t.get("requiere_lote", True)), campos=campos,
        )
    return CatalogoEventos(unidades=dict(unidades), tipos=tipos, campos=campos_globales)


@lru_cache
def catalogo_por_defecto() -> CatalogoEventos:
    return cargar_catalogo(get_settings().config_dir)


# ---------------------------------------------------------------------------
# Modelos para la salida estructurada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelosExtraccion:
    """Clases Pydantic generadas. `Extraccion` es la que se pasa a messages.parse."""

    Valor: type[BaseModel]
    Evento: type[BaseModel]
    Extraccion: type[BaseModel]


def _literal(valores: list[str]) -> Any:
    return Literal[tuple(valores)]  # type: ignore[valid-type]


def construir_modelos(catalogo: CatalogoEventos) -> ModelosExtraccion:
    estricto = ConfigDict(extra="forbid")
    TipoEvento = _literal(list(catalogo.tipos))
    Campo = _literal(list(catalogo.campos))
    Unidad = _literal(list(catalogo.unidades))
    Faltante = _literal(catalogo.faltantes_posibles())

    # Todos los campos son obligatorios en el esquema (anulables cuando aplica):
    # así no se gasta el límite de 24 opcionales y el orden de salida se respeta
    # (la API pone primero los obligatorios).
    Valor = create_model(
        "ValorMedido",
        __config__=estricto,
        campo=(Campo, Field(description="Nombre del campo, de la lista del tipo de evento.")),
        valor=(float | str, Field(
            description="Número (en cifras, con punto decimal) si el campo es numérico; "
                        "texto tal como lo dijo el productor si es de texto.")),
        unidad=(Unidad | None, Field(
            description="Unidad del valor, de la lista permitida para ese campo. "
                        "null en campos de texto.")),
    )
    Evento = create_model(
        "EventoExtraido",
        __config__=estricto,
        tipo_evento=(TipoEvento, Field(description="Tipo de evento del catálogo.")),
        fragmento=(str, Field(
            description="Cita textual corta del mensaje donde el productor cuenta este evento.")),
        lote_mencionado=(str | None, Field(
            description="Cómo nombró el lote, con sus palabras (\"el de arriba\", \"el 3\"). "
                        "null si no lo nombró.")),
        codigo_lote=(str | None, Field(
            description="Código de UNO de los lotes activos de la lista. null si no lo nombró, "
                        "si no está en la lista o si podría ser más de uno.")),
        fecha=(dt.date | None, Field(
            description="Día en que se hizo (AAAA-MM-DD). null si no se puede saber el día exacto.")),
        hora=(dt.time | None, Field(description="Hora si la dijo (HH:MM:SS). null si no.")),
        valores=(list[Valor], Field(  # type: ignore[valid-type]
            description="Solo los datos que el productor dijo. Lo que no dijo no se incluye.")),
        faltantes=(list[Faltante], Field(  # type: ignore[valid-type]
            description="Datos obligatorios de este evento que el productor no dijo.")),
    )
    Extraccion = create_model(
        "Extraccion",
        __config__=estricto,
        eventos=(list[Evento], Field(  # type: ignore[valid-type]
            description="Labores que ya se hicieron. Lista vacía si el mensaje no cuenta ninguna.")),
    )
    return ModelosExtraccion(Valor=Valor, Evento=Evento, Extraccion=Extraccion)


@lru_cache
def modelos_por_defecto() -> ModelosExtraccion:
    return construir_modelos(catalogo_por_defecto())
