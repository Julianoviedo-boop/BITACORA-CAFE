"""Límites de uso por productor y por organización (config/limites.yaml).

Cada mensaje atendido gasta CPU (whisper) y plata (API de Claude). Sin topes,
un teléfono registrado podría mandar miles de mensajes y disparar el gasto, o
dejar la cola ocupada para todas las organizaciones.

Dónde se revisa: en el primer paso del worker (tarea procesar_mensaje), después
del consentimiento y ANTES de transcribir o llamar a Claude. El webhook no se
toca: sigue respondiendo rápido.

Cómo se cuenta:
- Ventanas fijas: la hora y el día de Colombia, en Redis, con vencimiento.
- Se guarda el id de cada mensaje en un conjunto (SADD) y se cuenta su tamaño.
  Si la tarea se reintenta, el mismo mensaje no cuenta dos veces.
- Primero se cuenta al productor. Solo si está dentro de su tope se cuenta para
  la organización: así un productor que se pasa no le gasta el cupo a sus
  compañeros.
- Al pasarse, el mensaje queda en "error" (no se procesa) y se le avisa al
  productor UNA vez por ventana.
- Si Redis no responde, se deja pasar el mensaje y queda en el log: es mejor
  anotar de más que dejar de anotar a todos. Si Redis está caído, Celery tampoco
  funciona, así que este caso casi no se da.

También revisa el largo de los textos (max_caracteres_texto) antes de mandarlos
a Claude.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import yaml

from app.channels.base import Channel
from app.config import get_settings
from app.db.repositories import Repositories
from app.domain.transcripcion import enviar_aviso

logger = logging.getLogger(__name__)

ZONA_COLOMBIA = ZoneInfo("America/Bogota")
CLAVES_AVISO = ("limite_hora", "limite_dia", "texto_muy_largo")

NombreVentana = Literal["hora", "dia"]
Alcance = Literal["productor", "organizacion"]


# ---------------------------------------------------------------------------
# Reglas (config/limites.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tope:
    por_hora: int
    por_dia: int

    def de(self, ventana: NombreVentana) -> int:
        return self.por_hora if ventana == "hora" else self.por_dia


@dataclass(frozen=True)
class ReglasLimites:
    por_productor: Tope
    por_organizacion: Tope
    max_caracteres_texto: int
    max_tokens_respuesta: int
    avisos: dict[str, str]

    def aviso(self, clave: str) -> str:
        return self.avisos[clave].replace(
            "{max_caracteres}", f"{self.max_caracteres_texto:,}".replace(",", ".")).strip()


def _entero_positivo(path: Path, donde: str, valor: Any) -> int:
    if not isinstance(valor, int) or isinstance(valor, bool) or valor <= 0:
        raise ValueError(f"{path}: {donde} debe ser un entero mayor que 0")
    return valor


def _tope(path: Path, data: dict, clave: str) -> Tope:
    bloque = data.get(clave)
    if not isinstance(bloque, dict):
        raise ValueError(f"{path}: falta {clave} (con por_hora y por_dia)")
    hora = _entero_positivo(path, f"{clave}.por_hora", bloque.get("por_hora"))
    dia = _entero_positivo(path, f"{clave}.por_dia", bloque.get("por_dia"))
    if dia < hora:
        raise ValueError(f"{path}: {clave}.por_dia no puede ser menor que por_hora")
    return Tope(por_hora=hora, por_dia=dia)


def cargar_reglas(config_dir: str | Path) -> ReglasLimites:
    path = Path(config_dir) / "limites.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    avisos = data.get("avisos") or {}
    faltan = [c for c in CLAVES_AVISO if not str(avisos.get(c) or "").strip()]
    if faltan:
        raise ValueError(f"{path}: faltan los avisos {', '.join(faltan)}")
    return ReglasLimites(
        por_productor=_tope(path, data, "por_productor"),
        por_organizacion=_tope(path, data, "por_organizacion"),
        max_caracteres_texto=_entero_positivo(path, "max_caracteres_texto",
                                              data.get("max_caracteres_texto")),
        max_tokens_respuesta=_entero_positivo(path, "max_tokens_respuesta",
                                              data.get("max_tokens_respuesta")),
        avisos={c: str(avisos[c]) for c in CLAVES_AVISO},
    )


@lru_cache
def get_reglas() -> ReglasLimites:
    return cargar_reglas(get_settings().config_dir)


# ---------------------------------------------------------------------------
# Ventanas y contador
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ventana:
    nombre: NombreVentana
    id: str  # "2026111614" (hora) o "20261116" (día), hora de Colombia
    vida_s: int  # cuánto vive la llave en Redis: la ventana y un margen


def ventanas(ahora: dt.datetime) -> tuple[Ventana, Ventana]:
    if ahora.tzinfo is None:
        raise ValueError("La hora debe traer zona horaria")
    local = ahora.astimezone(ZONA_COLOMBIA)
    return (Ventana("hora", local.strftime("%Y%m%d%H"), 2 * 3600),
            Ventana("dia", local.strftime("%Y%m%d"), 2 * 86400))


class Contador(Protocol):
    def contar(self, llave: str, miembro: str, vida_s: int) -> int:
        """Agrega `miembro` al conjunto `llave` y devuelve cuántos tiene."""

    def marcar_una_vez(self, llave: str, vida_s: int) -> bool:
        """True solo la primera vez que se marca `llave` (mientras no venza)."""

    def desmarcar(self, llave: str) -> None: ...


class ContadorRedis:
    def __init__(self, cliente: Any) -> None:
        self._r = cliente

    @classmethod
    def desde_settings(cls) -> ContadorRedis:
        import redis

        return cls(redis.Redis.from_url(get_settings().redis_url, socket_timeout=2,
                                        socket_connect_timeout=2))

    def contar(self, llave: str, miembro: str, vida_s: int) -> int:
        pipe = self._r.pipeline()
        pipe.sadd(llave, miembro)
        pipe.expire(llave, vida_s)
        pipe.scard(llave)
        return int(pipe.execute()[2])

    def marcar_una_vez(self, llave: str, vida_s: int) -> bool:
        return bool(self._r.set(llave, "1", nx=True, ex=vida_s))

    def desmarcar(self, llave: str) -> None:
        self._r.delete(llave)


@dataclass(frozen=True)
class Excedido:
    alcance: Alcance
    ventana: Ventana
    tope: int


def revisar_cupo(contador: Contador, reglas: ReglasLimites, organization_id: uuid.UUID,
                 producer_id: uuid.UUID, message_id: uuid.UUID,
                 ahora: dt.datetime) -> Excedido | None:
    """Cuenta el mensaje y dice si se pasó de algún tope. Se revisa primero el
    día (su aviso es más útil: "mañana seguimos")."""
    hora, dia = ventanas(ahora)
    miembro = str(message_id)
    grupos: tuple[tuple[Alcance, str, Tope], ...] = (
        ("productor", f"{organization_id}:{producer_id}", reglas.por_productor),
        ("organizacion", str(organization_id), reglas.por_organizacion),
    )
    for alcance, ident, tope in grupos:
        excedido = None
        for v in (dia, hora):
            n = contador.contar(f"limite:{alcance}:{ident}:{v.nombre}:{v.id}", miembro, v.vida_s)
            if n > tope.de(v.nombre) and excedido is None:
                excedido = Excedido(alcance, v, tope.de(v.nombre))
        if excedido is not None:
            # Si el productor se pasó, no se cuenta para la organización.
            return excedido
    return None


# ---------------------------------------------------------------------------
# Caso de uso
# ---------------------------------------------------------------------------


def atender(
    repos: Repositories,
    channel: Channel,
    contador: Contador,
    reglas: ReglasLimites,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    ahora: dt.datetime,
) -> str | None:
    """None si el mensaje puede seguir. Si no, lo deja en "error", avisa y
    devuelve el motivo ("limite_excedido", "texto_muy_largo" u "omitido").

    El aviso sale dentro de la transacción de quien llama: si el envío falla, no
    queda nada cambiado y la tarea se reintenta (el conteo no se duplica).
    """
    msg = repos.messages.get(organization_id, message_id)
    if msg is None or msg.processing_status != "recibido" or msg.producer_id is None:
        return None  # lo resuelven los pasos siguientes, como hasta ahora

    try:
        excedido = revisar_cupo(contador, reglas, organization_id, msg.producer_id, msg.id, ahora)
    except Exception as exc:  # noqa: BLE001 - sin Redis se deja pasar (ver arriba)
        logger.warning("limites msg=%s no se pudo revisar el cupo; se deja pasar: %s",
                       message_id, exc)
        excedido = None

    if excedido is not None:
        v = excedido.ventana
        detalle = f"limite_{excedido.alcance}_{v.nombre}: tope {excedido.tope} ({v.id})"
        if repos.messages.set_processing_status(organization_id, msg.id, "error",
                                                error_detail=detalle,
                                                expected="recibido") is None:
            return "omitido"
        logger.warning("limites msg=%s org=%s productor=%s %s", message_id, organization_id,
                       msg.producer_id, detalle)
        llave = f"limite:aviso:{organization_id}:{msg.producer_id}:{v.nombre}:{v.id}"
        try:
            primera_vez = contador.marcar_una_vez(llave, v.vida_s)
        except Exception:  # noqa: BLE001 - sin Redis, mejor avisar de más que nunca
            primera_vez = True
        if primera_vez:
            try:
                enviar_aviso(repos, channel, organization_id, msg.id, reglas.aviso(f"limite_{v.nombre}"))
            except Exception:
                # El envío falló: la tarea se reintenta y debe poder avisar otra vez.
                try:
                    contador.desmarcar(llave)
                except Exception:  # noqa: BLE001
                    logger.warning("limites msg=%s no se pudo desmarcar el aviso", message_id)
                raise
        return "limite_excedido"

    if msg.message_type == "texto" and len(msg.text_body or "") > reglas.max_caracteres_texto:
        if repos.messages.set_processing_status(
                organization_id, msg.id, "error",
                error_detail=f"texto_muy_largo: {len(msg.text_body or '')} caracteres",
                expected="recibido") is None:
            return "omitido"
        enviar_aviso(repos, channel, organization_id, msg.id, reglas.aviso("texto_muy_largo"))
        return "texto_muy_largo"

    return None
