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

import datetime as dt
import logging
import string
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.channels import Channel, ChannelError, ChannelTemporaryError, dentro_de_ventana
from app.config import get_settings
from app.domain.consentimiento import normalizar
from app.extraction.prompts import ZONA_COLOMBIA
from app.extraction.schema import CatalogoEventos, catalogo_por_defecto

logger = logging.getLogger(__name__)

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
    # Cómo se llama en el panel ("Fermentación"). Si falta, sale de la clave.
    nombre: str | None = None
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


# ---------------------------------------------------------------------------
# 2. Reglas puras (sin base de datos)
# ---------------------------------------------------------------------------
#
# Reciben eventos con la forma de LotEvent (event_type, occurred_at, data, id,
# status, confirmed_at). Solo cuentan los confirmados (regla de oro 2).

_FIN = "__fin__"


@dataclass(frozen=True)
class EtapaLote:
    clave: str
    # Momento del evento que puso al lote en la etapa.
    desde: dt.datetime
    # Desde cuándo se cuentan los plazos (ver momento_para_plazos).
    desde_plazo: dt.datetime


def _orden(ev: Any) -> tuple:
    # Mismo momento: manda el orden en que se confirmaron (el del mensaje).
    return (ev.occurred_at, ev.confirmed_at or ev.occurred_at, ev.id)


def _confirmados(eventos: Iterable[Any]) -> list[Any]:
    return sorted((e for e in eventos if e.status == "confirmado"), key=_orden)


def _dato(ev: Any, campo: str) -> dict[str, Any] | None:
    dato = (ev.data or {}).get(campo)
    return dato if isinstance(dato, dict) else None


def _cumple(c: Condicion, ev: Any) -> bool:
    if ev.event_type != c.evento:
        return False
    if c.campo is None:
        return True
    texto = (_dato(ev, c.campo) or {}).get("valor")
    # Comienzo de palabra: "ferment" encuentra "despulpado y fermentación".
    return isinstance(texto, str) and f" {c.dice_normalizado}" in f" {normalizar(texto)}"


def efecto_del_evento(reglas: ReglasAlertas, ev: Any) -> str | None:
    """La etapa que empieza con el evento, _FIN si saca al lote de su etapa, o None
    si no cambia nada. Si empiezan varias, gana la que va más abajo en el YAML."""
    empiezan = [k for k, e in reglas.etapas.items() if any(_cumple(c, ev) for c in e.empieza_con)]
    if empiezan:
        return empiezan[-1]
    if any(_cumple(c, ev) for e in reglas.etapas.values() for c in e.termina_con):
        return _FIN
    return None


def etapa_del_lote(reglas: ReglasAlertas, eventos: Iterable[Any]) -> EtapaLote | None:
    """Etapa actual del lote: la dice el último evento confirmado que empieza o
    termina una etapa. None si no está en ninguna."""
    actual: EtapaLote | None = None
    for ev in _confirmados(eventos):
        efecto = efecto_del_evento(reglas, ev)
        if efecto == _FIN:
            actual = None
        elif efecto is not None:
            actual = EtapaLote(efecto, ev.occurred_at, momento_para_plazos(ev))
    return actual


def nombre_etapa(reglas: ReglasAlertas, clave: str) -> str:
    """El nombre de la etapa para el panel: `nombre` en el YAML o la clave legible."""
    etapa = reglas.etapas.get(clave)
    if etapa is not None and etapa.nombre:
        return etapa.nombre
    return clave.replace("_", " ").capitalize()


def etapa_del_evento(reglas: ReglasAlertas, eventos: Iterable[Any], evento: Any) -> str | None:
    """Etapa en la que se tomó el dato de `evento`: la que empieza con él o, si no
    empieza ninguna, en la que estaba el lote justo antes, aunque el evento la
    termine (la humedad final del secado se midió en el secado)."""
    efecto = efecto_del_evento(reglas, evento)
    if efecto not in (None, _FIN):
        return efecto
    clave = _orden(evento)
    etapa = etapa_del_lote(reglas, (e for e in eventos if e.id != evento.id and _orden(e) < clave))
    return etapa.clave if etapa else None


def valor_medido(regla: ReglaValor, ev: Any) -> float | None:
    """El número del campo de la regla en el evento, o None si no lo trae (o viene
    en otra unidad que la de la regla)."""
    dato = _dato(ev, regla.campo)
    if dato is None:
        return None
    valor = dato.get("valor")
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        return None
    if regla.unidad is not None and dato.get("unidad") not in (None, regla.unidad):
        return None
    return float(valor)


def fuera_de_rango(regla: ReglaValor, valor: float) -> bool:
    return ((regla.minimo is not None and valor < regla.minimo)
            or (regla.maximo is not None and valor > regla.maximo))


def momento_para_plazos(ev: Any) -> dt.datetime:
    """Desde cuándo cuenta un evento para las alertas por tiempo.

    Si el productor no dijo la hora, el evento queda a las 00:00 (hora de
    Colombia). Contar desde ahí haría saltar alertas antes de tiempo ("hoy empecé
    a fermentar" a las 9 de la mañana sonaría al mediodía). En ese caso se cuenta
    desde que lo confirmó, sin pasar del final de ese día.
    """
    local = ev.occurred_at.astimezone(ZONA_COLOMBIA)
    if local.timetz().replace(tzinfo=None) != dt.time(0) or ev.confirmed_at is None:
        return ev.occurred_at
    fin_del_dia = local + dt.timedelta(days=1)
    return max(ev.occurred_at, min(ev.confirmed_at, fin_del_dia))


def vencimiento(regla: ReglaTiempo, etapa: EtapaLote | None,
                eventos: Iterable[Any]) -> dt.datetime | None:
    """Hasta cuándo hay plazo para el evento esperado, o None si la regla no
    aplica (el lote no está en la etapa). Se cuenta desde el último evento
    esperado dentro de la etapa o, si no hay, desde que entró a ella."""
    if etapa is None or etapa.clave != regla.etapa:
        return None
    referencia = etapa.desde_plazo
    for ev in _confirmados(eventos):
        if ev.event_type == regla.evento_esperado and ev.occurred_at >= etapa.desde:
            referencia = max(referencia, momento_para_plazos(ev))
    return referencia + dt.timedelta(hours=regla.horas_limite)


def _numero(x: float) -> str:
    """12.5 -> "12,5"; 40.0 -> "40" (como se escribe en Colombia)."""
    return f"{x:g}".replace(".", ",")


def _nombre_lote(lote: Any) -> str:
    return lote.name or lote.code


def mensaje_por_valor(regla: ReglaValor, lote: Any, valor: float) -> str:
    vacio = "-"
    return regla.mensaje.format(
        lote=_nombre_lote(lote), valor=_numero(valor),
        minimo=_numero(regla.minimo) if regla.minimo is not None else vacio,
        maximo=_numero(regla.maximo) if regla.maximo is not None else vacio,
    )


def mensaje_por_tiempo(regla: ReglaTiempo, lote: Any) -> str:
    return regla.mensaje.format(lote=_nombre_lote(lote), horas=_numero(regla.horas_limite))


# ---------------------------------------------------------------------------
# 3. Casos de uso sobre la base
# ---------------------------------------------------------------------------
#
# Todos reciben `repos` (una transacción abierta) y filtran por organization_id
# (regla de oro 1). Devuelven los ids de las alertas NUEVAS, para que quien
# llama las mande por WhatsApp después del commit (tarea enviar_alerta).


def revisar_lote(repos: Any, reglas: ReglasAlertas, org_id: uuid.UUID, lote: Any,
                 ahora: dt.datetime, historia: Sequence[Any] | None = None) -> list[uuid.UUID]:
    """Abre las alertas por tiempo vencidas del lote y cierra las que ya no aplican."""
    if historia is None:
        historia = repos.events.list_by_lot(org_id, lote.id, status="confirmado")
    etapa = etapa_del_lote(reglas, historia)
    nuevas: list[uuid.UUID] = []

    for regla in reglas.alertas_por_tiempo:
        vence = vencimiento(regla, etapa, historia)
        if vence is not None and ahora >= vence:
            alerta = repos.alerts.open_if_absent(
                org_id, lot_id=lote.id, rule_code=regla.codigo,
                message=mensaje_por_tiempo(regla, lote), due_at=vence,
            )
            if alerta is not None:
                nuevas.append(alerta.id)
        else:
            # Llegó el evento esperado o el lote ya no está en la etapa.
            repos.alerts.resolve_open(org_id, lote.id, regla.codigo)

    if etapa is not None:
        # El lote volvió a empezar la etapa (otra tanda): las alertas por valor de
        # la tanda anterior ya no aplican.
        antes_de_empezar = etapa.desde - dt.timedelta(microseconds=1)
        for regla in reglas.alertas_por_valor:
            if regla.etapa == etapa.clave:
                repos.alerts.resolve_open(org_id, lote.id, regla.codigo,
                                          due_until=antes_de_empezar)
    return nuevas


def al_confirmar(repos: Any, reglas: ReglasAlertas, org_id: uuid.UUID,
                 evento_ids: Iterable[uuid.UUID], ahora: dt.datetime) -> list[uuid.UUID]:
    """Revisa los eventos que el productor acaba de confirmar.

    - Alertas por valor: abre una si el dato quedó fuera del rango; si quedó
      dentro, cierra la que estaba abierta.
    - Alertas por tiempo del lote: las revisa de una vez (p. ej. llegó el volteo).
    Los eventos que no están confirmados se ignoran (regla de oro 2).
    """
    eventos = [e for i in evento_ids if (e := repos.events.get(org_id, i)) is not None
               and e.status == "confirmado" and e.lot_id is not None]
    nuevas: list[uuid.UUID] = []
    for lot_id in dict.fromkeys(e.lot_id for e in eventos):
        lote = repos.lots.get(org_id, lot_id)
        if lote is None:
            continue
        historia = repos.events.list_by_lot(org_id, lot_id, status="confirmado")
        for ev in sorted((e for e in eventos if e.lot_id == lot_id), key=_orden):
            etapa = etapa_del_evento(reglas, historia, ev)
            for regla in reglas.alertas_por_valor:
                valor = valor_medido(regla, ev) if regla.etapa == etapa else None
                if valor is None:
                    continue
                if fuera_de_rango(regla, valor):
                    alerta = repos.alerts.open_if_absent(
                        org_id, lot_id=lot_id, rule_code=regla.codigo, event_id=ev.id,
                        message=mensaje_por_valor(regla, lote, valor), due_at=ev.occurred_at,
                    )
                    if alerta is not None:
                        nuevas.append(alerta.id)
                else:
                    repos.alerts.resolve_open(org_id, lot_id, regla.codigo,
                                              due_until=ev.occurred_at)
        nuevas += revisar_lote(repos, reglas, org_id, lote, ahora, historia)
    return nuevas


def revisar_tiempos(repos: Any, reglas: ReglasAlertas, org_id: uuid.UUID,
                    ahora: dt.datetime) -> list[uuid.UUID]:
    """Revisión periódica (Celery beat) de los lotes activos de una organización."""
    if not reglas.alertas_por_tiempo and not reglas.alertas_por_valor:
        return []
    nuevas: list[uuid.UUID] = []
    for lote in repos.lots.list_active(org_id):
        nuevas += revisar_lote(repos, reglas, org_id, lote, ahora)
    return nuevas


def enviar(repos: Any, channel: Channel, reglas: ReglasAlertas, org_id: uuid.UUID,
           alerta_id: uuid.UUID, ahora: dt.datetime) -> str:
    """Le manda la alerta a los productores de la finca del lote que autorizaron
    el tratamiento de datos.

    A cada uno, según su ventana de 24 horas (último mensaje que nos mandó):
      - dentro: texto normal;
      - fuera:  la plantilla aprobada, si está en config/alertas.yaml;
                si no hay plantilla, a ese productor no se le manda.
    Si no le llegó a nadie, la alerta sigue "pendiente": queda solo en el panel.

    Devuelve "enviada", "solo_panel" u "omitida" (ya no está pendiente: se cerró o
    ya se mandó; así un reintento de la tarea no la repite).
    """
    alerta = repos.alerts.get(org_id, alerta_id)
    if alerta is None or alerta.status != "pendiente":
        return "omitida"
    org = repos.organizations.get(org_id)
    lote = repos.lots.get(org_id, alerta.lot_id)
    if org is None or lote is None or not org.whatsapp_address:
        logger.warning("alerta=%s sin número de WhatsApp de la organización: solo panel",
                       alerta_id)
        return "solo_panel"

    plantilla = reglas.plantilla
    enviada = False
    for productor in repos.producers.list_by_farm(org_id, lote.farm_id,
                                                  consent_status="aceptado"):
        conv = repos.conversations.get_by_producer(org_id, productor.id)
        try:
            if dentro_de_ventana(conv.last_inbound_at if conv else None, ahora):
                channel.send_text(org.whatsapp_address, productor.phone, alerta.message)
            elif plantilla.nombre:
                channel.send_template(org.whatsapp_address, productor.phone, plantilla.nombre,
                                      plantilla.idioma, [alerta.message])
            else:
                logger.info("alerta=%s productor=%s fuera de la ventana de 24 h y sin "
                            "plantilla: no se le manda", alerta_id, productor.id)
                continue
        except ChannelTemporaryError:
            raise  # la tarea reintenta
        except ChannelError as exc:
            # Un número rechazado no impide avisarles a los demás.
            logger.error("alerta=%s productor=%s el canal rechazó el envío: %s",
                         alerta_id, productor.id, exc)
            continue
        enviada = True

    if not enviada:
        return "solo_panel"
    repos.alerts.mark_sent(org_id, alerta_id)
    return "enviada"
