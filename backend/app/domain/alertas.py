"""Motor de alertas de los lotes, configurado en config/alertas.yaml.

Dos clases de alerta (detalle en docs/decisiones.md, D-017):

- Por valor: un dato numérico de un evento CONFIRMADO quedó fuera del rango de
  la regla. Se evalúa justo cuando el productor confirma (regla de oro 2: un
  evento pendiente nunca dispara nada).
- Por tiempo: un lote lleva más de `horas_limite` en una etapa sin el evento
  esperado. Lo revisa Celery beat cada pocos minutos.

La etapa de un lote no se guarda: sale de sus eventos confirmados según
`etapas:` del YAML (ver `etapa_del_lote`).

Este archivo tiene tres partes:
1. Lectura y validación del YAML (Pydantic + revisión contra eventos.yaml).
2. Reglas puras: etapa del lote, valores fuera de rango, vencimientos.
3. Casos de uso sobre la base: abrir, cerrar y enviar alertas.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.config import get_settings
from app.domain.consentimiento import normalizar
from app.extraction.schema import CatalogoEventos, catalogo_por_defecto

# Llaves que se pueden usar en los mensajes de cada clase de alerta.
LLAVES_VALOR = frozenset({"lote", "valor", "minimo", "maximo"})
LLAVES_TIEMPO = frozenset({"lote", "horas"})
# Tope de horas_limite: más de 90 días ya no es una alerta útil (y suele ser un error).
MAX_HORAS_LIMITE = 24 * 90


class AlertasInvalidasError(ValueError):
    """alertas.yaml tiene errores. El mensaje los lista todos, uno por línea."""


# ---------------------------------------------------------------------------
# 1. Estructura del YAML
# ---------------------------------------------------------------------------


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class Condicion(_Modelo):
    """Un evento que mueve al lote de etapa: `evento` y, opcional, qué `dice` un campo."""

    evento: str
    campo: str | None = None
    dice: str | None = None

    @model_validator(mode="after")
    def _campo_y_dice_juntos(self) -> Condicion:
        if (self.campo is None) != (self.dice is None):
            raise ValueError("campo y dice van juntos: o se ponen los dos o ninguno")
        if self.dice is not None and not normalizar(self.dice):
            raise ValueError("dice no puede quedar vacío")
        return self

    @property
    def dice_normalizado(self) -> str | None:
        return normalizar(self.dice) if self.dice is not None else None


class Etapa(_Modelo):
    empieza_con: list[Condicion] = Field(min_length=1)
    termina_con: list[Condicion] = Field(default_factory=list)


class ReglaValor(_Modelo):
    etapa: str
    campo: str
    minimo: float | None = None
    maximo: float | None = None
    # Solo hace falta si el campo admite varias unidades en eventos.yaml.
    unidad: str | None = None
    mensaje: str = Field(min_length=1)

    @model_validator(mode="after")
    def _rango(self) -> ReglaValor:
        if self.minimo is None and self.maximo is None:
            raise ValueError("necesita minimo, maximo o los dos")
        if self.minimo is not None and self.maximo is not None and self.minimo >= self.maximo:
            raise ValueError(f"minimo ({self.minimo:g}) debe ser menor que maximo ({self.maximo:g})")
        return self

    @property
    def codigo(self) -> str:
        """Lo que se guarda en alerts.rule_code. Se arma solo: nadie lo escribe."""
        return f"valor:{self.etapa}:{self.campo}"


class ReglaTiempo(_Modelo):
    etapa: str
    evento_esperado: str
    horas_limite: float = Field(gt=0, le=MAX_HORAS_LIMITE)
    mensaje: str = Field(min_length=1)

    @property
    def codigo(self) -> str:
        return f"tiempo:{self.etapa}:{self.evento_esperado}"


class Plantilla(_Modelo):
    # Vacío: fuera de la ventana de 24 horas la alerta queda solo en el panel.
    nombre: str = ""
    idioma: str = Field(default="es", min_length=2)


class ReglasAlertas(_Modelo):
    etapas: dict[str, Etapa] = Field(default_factory=dict)
    alertas_por_valor: list[ReglaValor] = Field(default_factory=list)
    alertas_por_tiempo: list[ReglaTiempo] = Field(default_factory=list)
    plantilla: Plantilla = Field(default_factory=Plantilla)


# ---------------------------------------------------------------------------
# Lectura y errores claros
# ---------------------------------------------------------------------------

# Errores de Pydantic traducidos para quien edita el YAML (no programa).
_TRADUCCIONES: dict[str, str] = {
    "missing": "falta este dato",
    "extra_forbidden": "no se reconoce este dato (¿está bien escrito?)",
    "float_parsing": "debe ser un número; los decimales van con punto (12.5, no 12,5)",
    "float_type": "debe ser un número; los decimales van con punto (12.5, no 12,5)",
    "string_type": "debe ser un texto",
    "too_short": "no puede quedar vacío",
    "list_type": "debe ser una lista: cada elemento empieza con un guion (-)",
    "dict_type": "debe tener sus datos debajo, con sangría",
    "model_type": "debe tener sus datos debajo, con sangría",
    "model_attributes_type": "debe tener sus datos debajo, con sangría",
}


def _donde(loc: tuple[Any, ...]) -> str:
    partes: list[str] = []
    for i, p in enumerate(loc):
        anterior = loc[i - 1] if i else None
        if isinstance(p, int):
            nombre = "regla" if anterior in ("alertas_por_valor", "alertas_por_tiempo") else "condición"
            partes.append(f"{nombre} {p + 1}")
        else:
            partes.append(str(p))
    return " → ".join(partes) or "el archivo"


def _texto_error(err: dict[str, Any]) -> str:
    tipo, ctx = err["type"], err.get("ctx") or {}
    if tipo == "value_error":
        return str(ctx.get("error", err["msg"]))
    if tipo == "greater_than":
        return f"debe ser mayor que {ctx['gt']}"
    if tipo == "less_than_equal":
        return f"no puede pasar de {ctx['le']}"
    return _TRADUCCIONES.get(tipo, err["msg"])


def _llaves(mensaje: str) -> set[str]:
    """Llaves {así} del mensaje. Lanza ValueError si una llave está mal cerrada o
    trae formato ({valor:.1f}), que no se admite para no confundir a quien edita."""
    llaves: set[str] = set()
    for _, campo, formato, conversion in string.Formatter().parse(mensaje):
        if campo is None:
            continue
        if formato or conversion or not campo.isidentifier():
            raise ValueError(f"la llave {{{campo}}} está mal escrita")
        llaves.add(campo)
    return llaves


def _revisar_mensaje(donde: str, mensaje: str, permitidas: frozenset[str]) -> list[str]:
    try:
        llaves = _llaves(mensaje)
    except ValueError as exc:
        texto = str(exc) if "llave" in str(exc) else "hay una llave { o } sin cerrar"
        return [f"{donde} → mensaje: {texto}"]
    sobran = sorted(llaves - permitidas)
    if sobran:
        validas = ", ".join(f"{{{x}}}" for x in sorted(permitidas))
        return [f"{donde} → mensaje: {{{sobran[0]}}} no se puede usar aquí; se puede usar {validas}"]
    return []


def _unidades_del_campo(catalogo: CatalogoEventos, campo: str) -> set[str]:
    return {u for t in catalogo.tipos.values() if campo in t.campos for u in t.campos[campo].unidades}


def _revisar_contra_catalogo(reglas: ReglasAlertas, catalogo: CatalogoEventos) -> list[str]:
    """Lo que Pydantic no puede saber solo: que los nombres existan en eventos.yaml."""
    errores: list[str] = []
    tipos = catalogo.tipos

    def _evento(donde: str, evento: str) -> bool:
        if evento not in tipos:
            errores.append(f"{donde} → evento: '{evento}' no existe en config/eventos.yaml "
                           f"(hay: {', '.join(tipos)})")
            return False
        return True

    for clave, etapa in reglas.etapas.items():
        if not (clave.isidentifier() and clave.isascii() and clave.islower()):
            errores.append(f"etapas → {clave}: el nombre va en minúsculas, sin tildes ni espacios "
                           "(p. ej. fermentacion)")
        for grupo in ("empieza_con", "termina_con"):
            for i, c in enumerate(getattr(etapa, grupo)):
                donde = f"etapas → {clave} → {grupo} → condición {i + 1}"
                if not _evento(donde, c.evento) or c.campo is None:
                    continue
                campo = tipos[c.evento].campos.get(c.campo)
                if campo is None:
                    errores.append(f"{donde} → campo: el evento '{c.evento}' no tiene el campo "
                                   f"'{c.campo}' (tiene: {', '.join(tipos[c.evento].campos) or 'ninguno'})")
                elif campo.tipo != "texto":
                    errores.append(f"{donde} → campo: '{c.campo}' es un número; "
                                   "dice solo sirve con campos de texto")

    def _etapa(donde: str, etapa: str) -> None:
        if etapa not in reglas.etapas:
            hay = ", ".join(reglas.etapas) or "ninguna"
            errores.append(f"{donde} → etapa: '{etapa}' no está en etapas (hay: {hay})")

    codigos: set[str] = set()

    def _repetida(donde: str, codigo: str) -> None:
        if codigo in codigos:
            errores.append(f"{donde}: está repetida (misma etapa y mismo dato que otra regla)")
        codigos.add(codigo)

    for i, r in enumerate(reglas.alertas_por_valor):
        donde = f"alertas_por_valor → regla {i + 1} ({r.etapa} / {r.campo})"
        _etapa(donde, r.etapa)
        _repetida(donde, r.codigo)
        errores += _revisar_mensaje(donde, r.mensaje, LLAVES_VALOR)
        tipo_campo = catalogo.campos.get(r.campo)
        if tipo_campo is None:
            errores.append(f"{donde} → campo: '{r.campo}' no existe en ningún evento de "
                           "config/eventos.yaml")
            continue
        if tipo_campo != "numero":
            errores.append(f"{donde} → campo: '{r.campo}' es de texto; solo se revisan números")
            continue
        unidades = _unidades_del_campo(catalogo, r.campo)
        if r.unidad is None and len(unidades) > 1:
            errores.append(f"{donde} → unidad: '{r.campo}' se puede medir en "
                           f"{', '.join(sorted(unidades))}; diga cuál con unidad:")
        elif r.unidad is not None and r.unidad not in unidades:
            errores.append(f"{donde} → unidad: '{r.campo}' no se mide en '{r.unidad}' "
                           f"(se mide en {', '.join(sorted(unidades))})")

    for i, r in enumerate(reglas.alertas_por_tiempo):
        donde = f"alertas_por_tiempo → regla {i + 1} ({r.etapa} / {r.evento_esperado})"
        _etapa(donde, r.etapa)
        _repetida(donde, r.codigo)
        errores += _revisar_mensaje(donde, r.mensaje, LLAVES_TIEMPO)
        if r.evento_esperado not in tipos:
            errores.append(f"{donde} → evento_esperado: '{r.evento_esperado}' no existe en "
                           f"config/eventos.yaml (hay: {', '.join(tipos)})")
    return errores


def cargar_reglas(config_dir: str | Path, catalogo: CatalogoEventos) -> ReglasAlertas:
    """Lee y valida config/alertas.yaml. Lanza AlertasInvalidasError con TODOS los
    errores encontrados (así quien edita los arregla de una vez)."""
    path = Path(config_dir) / "alertas.yaml"
    titulo = f"{path} tiene errores:"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AlertasInvalidasError(f"No se encontró {path}") from exc
    except yaml.YAMLError as exc:
        marca = getattr(exc, "problem_mark", None)
        linea = f" (línea {marca.line + 1})" if marca is not None else ""
        raise AlertasInvalidasError(
            f"{titulo}\n  - el formato está dañado{linea}: revise la sangría, los dos puntos "
            "y que los textos con : o # vayan entre comillas"
        ) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise AlertasInvalidasError(f"{titulo}\n  - debe empezar con etapas:, alertas_por_valor:...")

    try:
        reglas = ReglasAlertas.model_validate(data)
    except ValidationError as exc:
        lineas = [f"{_donde(e['loc'])}: {_texto_error(e)}" for e in exc.errors()]
        raise AlertasInvalidasError("\n  - ".join([titulo, *lineas])) from exc

    errores = _revisar_contra_catalogo(reglas, catalogo)
    if errores:
        raise AlertasInvalidasError("\n  - ".join([titulo, *errores]))
    return reglas


@lru_cache
def get_reglas() -> ReglasAlertas:
    return cargar_reglas(get_settings().config_dir, catalogo_por_defecto())


def validar_al_arrancar() -> None:
    """Se llama al arrancar la API, el worker y beat: si alertas.yaml tiene errores,
    el proceso no arranca y el log dice exactamente qué arreglar."""
    get_reglas()
