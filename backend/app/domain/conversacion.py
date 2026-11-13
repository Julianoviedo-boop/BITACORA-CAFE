"""Conversación con el productor: de un mensaje a eventos confirmados.

Flujo (cada mensaje lo atiende la tarea `atender_conversacion`, después del
consentimiento y, si es audio, de la transcripción):

    mensaje -> extracción (Claude) -> borradores
      -> falta un dato obligatorio: se pregunta UNO a la vez
      -> nombró un lote que no existe: se ofrece crearlo
      -> no falta nada: eventos "pendiente" + resumen de una línea por evento
      -> SÍ: se confirman | corrección: se actualizan | CANCELAR: se rechazan

- Correcciones: máximo `max_rondas` después del resumen; luego se le pide hablar
  con el asesor (los eventos quedan "pendiente" para que el asesor los revise).
  Lo mismo si se le repite `max_rondas` veces la misma pregunta.
- Comandos en cualquier momento: "mis lotes", "resumen lote 3", "ayuda". No
  cambian el estado; si había una pregunta abierta, se le recuerda.
- Regla de oro 2: `events.confirm` solo se llama en `_ejecutar` con la acción
  "confirmar", que solo sale de una respuesta de la lista `respuestas.confirmar`
  estando en estado "confirmando" (el resumen ya se le mostró).

Estado: tabla `conversations`, una fila por productor (sobrevive a reinicios del
worker). Para no dejar una transacción abierta mientras se habla con Claude:

    1. leer   (transacción corta): mensaje, conversación (con su `version`), lotes.
    2. Claude (sin transacción), solo si hace falta.
    3. aplicar (transacción con la fila de la conversación bloqueada): si la
       `version` cambió entretanto (otro worker atendió otro mensaje del mismo
       productor), no se toca nada y se vuelve a empezar.

Las respuestas se mandan dentro de la transacción del paso 3: si el envío
falla, no queda ningún cambio y la tarea se reintenta.

Textos, palabras clave y límites: config/conversacion.yaml. Preguntas por campo:
config/eventos.yaml (regla de oro 5).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.channels import Channel, dentro_de_ventana
from app.config import get_settings
from app.db.models import Conversation, InboundMessage, Lot, LotEvent, Organization, Producer
from app.db.repositories import InvalidTransitionError, Repositories
from app.domain.consentimiento import normalizar
from app.extraction import extractor
from app.extraction.extractor import DatoMedido, EventoPropuesto, lotes_activos
from app.extraction.llm import ClienteLLM, ErrorLLM
from app.extraction.prompts import ZONA_COLOMBIA, LoteContexto, a_hora_colombia
from app.extraction.schema import FALTANTE_FECHA, FALTANTE_LOTE, CatalogoEventos

logger = logging.getLogger(__name__)

UnitOfWork = Callable[[], AbstractContextManager[Repositories]]

# Veces que se vuelve a empezar si la conversación cambió mientras se hablaba con Claude.
MAX_CONFLICTOS = 3
# Eventos que muestra "resumen lote X".
RESUMEN_MAX_EVENTOS = 5
# Después de "resumen", más palabras que esto ya no es un lote sino un reporte.
RESUMEN_MAX_PALABRAS_LOTE = 4

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


class ReintentarError(Exception):
    """Falla temporal: Claude no respondió o la conversación cambió varias veces.
    La tarea de Celery reintenta."""


# ---------------------------------------------------------------------------
# Textos (config/conversacion.yaml)
# ---------------------------------------------------------------------------

CLAVES_TEXTOS = (
    "confirmar_uno", "confirmar_varios", "pregunta_dato", "ofrecer_lote", "lote_creado",
    "confirmado", "cancelado", "nada_pendiente", "que_cambiar", "no_entendi",
    "no_entendi_correccion", "asesor", "error", "solo_audio_texto", "recordatorio", "ayuda",
    "mis_lotes", "sin_lotes", "resumen", "resumen_vacio", "resumen_sin_lote",
    "lote_no_encontrado",
)


@dataclass(frozen=True)
class TextosConversacion:
    max_rondas: int
    confirmar: frozenset[str]
    negar: frozenset[str]
    cancelar: frozenset[str]
    mis_lotes: frozenset[str]
    ayuda: frozenset[str]
    # Normalizados, del más largo al más corto.
    prefijos_resumen: tuple[str, ...]
    preguntas: dict[str, str]
    # unidad -> (singular, plural)
    unidades: dict[str, tuple[str, str]]
    textos: dict[str, str]

    def t(self, clave: str, **valores: object) -> str:
        texto = self.textos[clave]
        for k, v in valores.items():
            texto = texto.replace("{" + k + "}", str(v))
        return texto.strip()


def _lista(path: Path, donde: str, valor: Any) -> frozenset[str]:
    if not isinstance(valor, list) or not valor:
        raise ValueError(f"{path}: {donde} debe ser una lista con al menos un texto")
    raras = [v for v in valor if not isinstance(v, str)]
    if raras:
        # YAML lee  no / si / yes  sin comillas como booleanos.
        raise ValueError(f"{path}: {donde} debe tener textos entre comillas; llegó {raras}")
    return frozenset(normalizar(v) for v in valor) - {""}


def cargar_textos(config_dir: str | Path) -> TextosConversacion:
    path = Path(config_dir) / "conversacion.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    max_rondas = data.get("max_rondas")
    if not isinstance(max_rondas, int) or isinstance(max_rondas, bool) or max_rondas <= 0:
        raise ValueError(f"{path}: max_rondas debe ser un entero mayor que 0")

    respuestas = data.get("respuestas") or {}
    comandos = data.get("comandos") or {}
    grupos = {
        "respuestas.confirmar": _lista(path, "respuestas.confirmar", respuestas.get("confirmar")),
        "respuestas.negar": _lista(path, "respuestas.negar", respuestas.get("negar")),
        "respuestas.cancelar": _lista(path, "respuestas.cancelar", respuestas.get("cancelar")),
        "comandos.mis_lotes": _lista(path, "comandos.mis_lotes", comandos.get("mis_lotes")),
        "comandos.ayuda": _lista(path, "comandos.ayuda", comandos.get("ayuda")),
    }
    vistos: dict[str, str] = {}
    for nombre, valores in grupos.items():
        for v in valores:
            if v in vistos:
                raise ValueError(f"{path}: «{v}» está en {vistos[v]} y en {nombre}")
            vistos[v] = nombre
    prefijos = _lista(path, "comandos.resumen", comandos.get("resumen"))

    preguntas = data.get("preguntas") or {}
    faltan = [k for k in ("lote", "fecha", "generica") if not str(preguntas.get(k) or "").strip()]
    if faltan:
        raise ValueError(f"{path}: faltan las preguntas {', '.join(faltan)}")

    unidades: dict[str, tuple[str, str]] = {}
    for clave, valor in (data.get("unidades") or {}).items():
        if isinstance(valor, str):
            unidades[clave] = (valor, valor)
        elif isinstance(valor, list) and len(valor) == 2 and all(isinstance(v, str) for v in valor):
            unidades[clave] = (valor[0], valor[1])
        else:
            raise ValueError(f"{path}: unidades.{clave} debe ser un texto o [singular, plural]")

    textos = data.get("textos") or {}
    faltan = [k for k in CLAVES_TEXTOS if not str(textos.get(k) or "").strip()]
    if faltan:
        raise ValueError(f"{path}: faltan los textos {', '.join(faltan)}")

    return TextosConversacion(
        max_rondas=max_rondas,
        confirmar=grupos["respuestas.confirmar"],
        negar=grupos["respuestas.negar"],
        cancelar=grupos["respuestas.cancelar"],
        mis_lotes=grupos["comandos.mis_lotes"],
        ayuda=grupos["comandos.ayuda"],
        prefijos_resumen=tuple(sorted(prefijos, key=len, reverse=True)),
        preguntas={k: str(preguntas[k]).strip() for k in ("lote", "fecha", "generica")},
        unidades=unidades,
        textos={k: str(textos[k]) for k in CLAVES_TEXTOS},
    )


@lru_cache
def get_textos() -> TextosConversacion:
    return cargar_textos(get_settings().config_dir)


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


@dataclass
class Interprete:
    """Saca los eventos de un texto (o los corrige) con Claude.

    En las pruebas se le pasa un ClienteLLM con un cliente de Anthropic falso:
    así se ejercita todo (prompt, esquema, normalización) sin red.
    """

    catalogo: CatalogoEventos
    cliente: ClienteLLM | None = None

    def extraer(self, texto: str, recibido_en: dt.datetime,
                lotes: Sequence[LoteContexto]) -> list[EventoPropuesto]:
        return extractor.extraer(texto, recibido_en=recibido_en, lotes=lotes,
                                 cliente=self.cliente, catalogo=self.catalogo).eventos

    def revisar(self, eventos: Sequence[EventoPropuesto], texto: str,
                recibido_en: dt.datetime, lotes: Sequence[LoteContexto],
                pregunta: str | None) -> list[EventoPropuesto]:
        return extractor.revisar(eventos, texto, recibido_en=recibido_en, lotes=lotes,
                                 pregunta=pregunta, cliente=self.cliente,
                                 catalogo=self.catalogo).eventos


# ---------------------------------------------------------------------------
# Formato (lo que lee el productor)
# ---------------------------------------------------------------------------


def formatear_numero(valor: float | int) -> str:
    """Como se escribe en Colombia: 2,5 y 2.000.000."""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    signo = "-" if valor < 0 else ""
    entero, _, decimales = f"{abs(valor):.2f}".partition(".")
    if isinstance(valor, int):
        decimales = ""
    entero = f"{int(entero):,}".replace(",", ".")
    decimales = decimales.rstrip("0")
    return f"{signo}{entero},{decimales}" if decimales else f"{signo}{entero}"


def formatear_hora(hora: dt.time) -> str:
    h = hora.hour % 12 or 12
    return f"{h}:{hora.minute:02d} {'a.m.' if hora.hour < 12 else 'p.m.'}"


def formatear_fecha(fecha: dt.date, hoy: dt.date) -> str:
    relativa = {0: "hoy", 1: "ayer", 2: "antier"}.get((hoy - fecha).days)
    if relativa:
        return relativa
    texto = f"{_DIAS[fecha.weekday()]} {fecha.day} de {_MESES[fecha.month - 1]}"
    return texto if fecha.year == hoy.year else f"{texto} de {fecha.year}"


def formatear_dato(dato: DatoMedido, textos: TextosConversacion,
                   catalogo: CatalogoEventos) -> str:
    if isinstance(dato.valor, str):
        return dato.valor
    numero = formatear_numero(dato.valor)
    if not dato.unidad:
        return numero
    singular, plural = textos.unidades.get(
        dato.unidad, (catalogo.unidades.get(dato.unidad, dato.unidad),) * 2)
    return f"{numero} {singular if dato.valor == 1 else plural}"


def linea_evento(tipo_evento: str, lote: str | None, fecha: dt.date | None,
                 hora: dt.time | None, datos: dict[str, DatoMedido | None], hoy: dt.date,
                 textos: TextosConversacion, catalogo: CatalogoEventos) -> str:
    """Una línea por evento: "Lote 3: abonada, hoy 7:30 a.m., 17-6-18, 4 bultos"."""
    tipo = catalogo.tipos.get(tipo_evento)
    nombre = tipo.nombre if tipo else tipo_evento
    partes = [f"{lote}: {nombre.lower()}" if lote else nombre]
    cuando = " ".join(p for p in (formatear_fecha(fecha, hoy) if fecha else None,
                                  formatear_hora(hora) if hora else None) if p)
    if cuando:
        partes.append(cuando)
    # En el orden del catálogo; lo que ya no esté en el catálogo, al final.
    orden = list(tipo.campos) if tipo else []
    orden += [c for c in datos if c not in orden]
    partes += [formatear_dato(d, textos, catalogo) for c in orden if (d := datos.get(c))]
    return ", ".join(partes)


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------

_ARTICULOS = re.compile(r"^(?:(?:del|de|el|la)\s+)?(?:lote\s+)?")


def _numero_final(texto: str) -> int | None:
    m = re.search(r"(\d+)\s*$", texto)
    return int(m.group(1)) if m else None


def _variantes(texto: str) -> set[str]:
    t = normalizar(texto)
    return {t, _ARTICULOS.sub("", t).strip()} - {""}


def buscar_lote(referencia: str, lotes: Sequence[Lot]) -> Lot | None:
    """Lote por nombre, código o número ("3" = el que termina en 3). None si no hay
    uno solo."""
    ref = _variantes(referencia)
    if not ref:
        return None
    exactos = {l.id: l for l in lotes
               if ref & (_variantes(l.name) | _variantes(l.code) | {f"lote {normalizar(l.name)}"})}
    if len(exactos) == 1:
        return next(iter(exactos.values()))
    numeros = {_numero_final(r) for r in ref if r.isdigit()}
    if len(numeros) == 1:
        n = numeros.pop()
        por_numero = {l.id: l for l in lotes
                      if n in (_numero_final(normalizar(l.code)), _numero_final(normalizar(l.name)))}
        if len(por_numero) == 1:
            return next(iter(por_numero.values()))
    return None


def nombre_lote_nuevo(mencion: str) -> str:
    """"el 3" / "lote tres"... -> "Lote 3"; "el de arriba" -> "El de arriba"."""
    m = re.fullmatch(r"(?:(?:el|la|del)\s+)?(?:lote\s+)?(?:numero\s+)?(\d+)", normalizar(mencion))
    if m:
        return f"Lote {int(m.group(1))}"
    texto = re.sub(r"^(?:(?:el|la|del)\s+lote|lote)\s+", "", mencion.strip(), flags=re.I)
    return (texto[:1].upper() + texto[1:])[:200]


# ---------------------------------------------------------------------------
# Qué quiere el productor (sin Claude)
# ---------------------------------------------------------------------------

Intencion = Literal["vacio", "mis_lotes", "ayuda", "resumen", "confirmar", "negar",
                    "cancelar", "texto"]
Accion = Literal["vacio", "mis_lotes", "ayuda", "resumen", "nada_pendiente", "confirmar",
                 "que_cambiar", "cancelar", "crear_lote", "rechazar_lote", "asesor",
                 "extraer", "revisar"]


def clasificar(texto: str | None, textos: TextosConversacion) -> tuple[Intencion, str | None]:
    t = normalizar(texto or "")
    if not t:
        return "vacio", None
    if t in textos.mis_lotes:
        return "mis_lotes", None
    if t in textos.ayuda:
        return "ayuda", None
    for prefijo in textos.prefijos_resumen:
        if t == prefijo:
            return "resumen", None
        if t.startswith(prefijo + " "):
            resto = t[len(prefijo) + 1:]
            if len(resto.split()) <= RESUMEN_MAX_PALABRAS_LOTE:
                return "resumen", resto
            break  # texto largo: es un reporte ("resumen de la semana: ...")
    if t in textos.cancelar:
        return "cancelar", None
    if t in textos.confirmar:
        return "confirmar", None
    if t in textos.negar:
        return "negar", None
    return "texto", None


def planear(estado: str, rondas: int, intencion: Intencion,
            textos: TextosConversacion) -> Accion:
    if intencion in ("vacio", "mis_lotes", "ayuda", "resumen"):
        return intencion
    if estado == "libre":
        return "nada_pendiente" if intencion in ("confirmar", "negar", "cancelar") else "extraer"
    if intencion == "cancelar":
        return "cancelar"
    if estado == "confirmando":
        if intencion == "confirmar":
            return "confirmar"
        if intencion == "negar":
            return "que_cambiar"
        return "asesor" if rondas >= textos.max_rondas else "revisar"
    if estado == "ofreciendo_lote":
        if intencion == "confirmar":
            return "crear_lote"
        if intencion == "negar":
            return "rechazar_lote"
    # Respuesta a una pregunta (o al ofrecimiento con otro lote): la entiende Claude.
    return "revisar"


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------


@dataclass
class _Foto:
    """Lo leído en la primera transacción, antes de hablar con Claude."""

    producer_id: uuid.UUID
    estado_msg: str
    texto: str | None
    recibido_en: dt.datetime
    version: int
    estado: str
    borradores: list[EventoPropuesto]
    rondas: int
    ultimo_texto: str | None
    lotes: list[LoteContexto]


@dataclass
class _Estado:
    """Copia de trabajo de la conversación (se guarda al final)."""

    estado: str
    borradores: list[EventoPropuesto]
    evento_ids: list[uuid.UUID]
    pregunta: dict[str, Any] | None
    rondas: int
    origen: uuid.UUID | None
    ultimo_texto: str | None
    asesor_at: dt.datetime | None

    @classmethod
    def desde(cls, conv: Conversation) -> _Estado:
        return cls(
            estado=conv.state,
            borradores=_borradores(conv),
            evento_ids=[uuid.UUID(str(i)) for i in conv.event_ids or []],
            pregunta=dict(conv.question) if conv.question else None,
            rondas=conv.correction_rounds,
            origen=conv.source_message_id,
            ultimo_texto=conv.last_prompt,
            asesor_at=conv.advisor_requested_at,
        )

    def limpiar(self) -> None:
        self.estado, self.borradores, self.evento_ids = "libre", [], []
        self.pregunta, self.rondas, self.origen, self.ultimo_texto = None, 0, None, None


def _borradores(conv: Conversation) -> list[EventoPropuesto]:
    return [EventoPropuesto.model_validate(b) for b in conv.drafts or []]


def _estado_listo(msg: InboundMessage) -> str:
    """Estado en que un mensaje le llega a la conversación."""
    return "transcrito" if msg.message_type == "audio" else "recibido"


@dataclass
class _Ctx:
    repos: Repositories
    channel: Channel
    textos: TextosConversacion
    catalogo: CatalogoEventos
    org: Organization
    producer: Producer
    msg: InboundMessage
    ahora: dt.datetime

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def hoy(self) -> dt.date:
        return a_hora_colombia(self.ahora).date()


# ---------------------------------------------------------------------------
# Caso de uso
# ---------------------------------------------------------------------------


def atender(
    uow: UnitOfWork,
    channel: Channel,
    interprete: Interprete,
    textos: TextosConversacion,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    ahora: dt.datetime | None = None,
) -> str:
    """Atiende un mensaje ya listo (texto con consentimiento, o audio transcrito).

    Devuelve qué se hizo ("resumen", "pregunta", "confirmado", "omitido"...).
    Lanza ReintentarError si Claude falló de forma temporal.
    """
    for _ in range(MAX_CONFLICTOS):
        with uow() as repos:
            foto = _leer(repos, organization_id, message_id)
        if foto is None:
            return "omitido"

        intencion, referencia = clasificar(foto.texto, textos)
        accion = planear(foto.estado, foto.rondas, intencion, textos)
        try:
            propuesta = _interpretar(accion, foto, interprete)
        except ErrorLLM as exc:
            if exc.temporal:
                raise ReintentarError(str(exc)) from exc
            logger.error("conversacion msg=%s error de extraccion: %s", message_id, exc)
            return fallar(uow, channel, textos, organization_id, message_id,
                          f"extraccion: {exc}", ahora=ahora)

        with uow() as repos:
            resultado = _aplicar(repos, channel, interprete.catalogo, textos, organization_id,
                                 message_id, foto, accion, referencia, propuesta,
                                 ahora or dt.datetime.now(dt.UTC))
        if resultado != "conflicto":
            logger.info("conversacion msg=%s estado=%s accion=%s resultado=%s", message_id,
                        foto.estado, accion, resultado)
            return resultado
        logger.info("conversacion msg=%s la conversación cambió mientras tanto; otra vez",
                    message_id)
    raise ReintentarError("La conversación cambió varias veces mientras se atendía el mensaje")


def _leer(repos: Repositories, org_id: uuid.UUID, msg_id: uuid.UUID) -> _Foto | None:
    msg = repos.messages.get(org_id, msg_id)
    if msg is None or msg.producer_id is None or msg.processing_status != _estado_listo(msg):
        return None
    producer = repos.producers.get(org_id, msg.producer_id)
    # Se vuelve a revisar: el productor pudo revocar la autorización entretanto.
    if producer is None or producer.consent_status != "aceptado":
        return None
    conv = repos.conversations.get_or_create(org_id, producer.id)
    texto = msg.transcription if msg.message_type == "audio" else msg.text_body
    return _Foto(
        producer_id=producer.id, estado_msg=msg.processing_status, texto=texto,
        recibido_en=msg.received_at, version=conv.version, estado=conv.state,
        borradores=_borradores(conv), rondas=conv.correction_rounds,
        ultimo_texto=conv.last_prompt, lotes=lotes_activos(repos, org_id),
    )


def _interpretar(accion: Accion, foto: _Foto,
                 interprete: Interprete) -> list[EventoPropuesto] | None:
    if accion == "extraer":
        return interprete.extraer(foto.texto or "", foto.recibido_en, foto.lotes)
    if accion == "revisar":
        return interprete.revisar(foto.borradores, foto.texto or "", foto.recibido_en,
                                  foto.lotes, foto.ultimo_texto)
    return None


def _aplicar(repos: Repositories, channel: Channel, catalogo: CatalogoEventos,
             textos: TextosConversacion, org_id: uuid.UUID, msg_id: uuid.UUID, foto: _Foto,
             accion: Accion, referencia: str | None,
             propuesta: list[EventoPropuesto] | None, ahora: dt.datetime) -> str:
    conv = repos.conversations.get_by_producer(org_id, foto.producer_id, for_update=True)
    if conv is None or conv.version != foto.version:
        return "conflicto"
    # Se toma el mensaje: si otro worker ya lo atendió, no se hace nada.
    msg = repos.messages.set_processing_status(org_id, msg_id, "procesado",
                                               expected=foto.estado_msg)
    if msg is None:
        return "omitido"
    org = repos.organizations.get(org_id)
    producer = repos.producers.get(org_id, foto.producer_id)
    assert org is not None and producer is not None

    ctx = _Ctx(repos, channel, textos, catalogo, org, producer, msg, ahora)
    estado = _Estado.desde(conv)
    texto, resultado = _ejecutar(ctx, estado, accion, referencia, propuesta)

    ultimo_entrante = max(t for t in (conv.last_inbound_at, msg.received_at) if t)
    cambios: dict[str, Any] = {
        "state": estado.estado,
        "drafts": [b.model_dump(mode="json") for b in estado.borradores],
        "event_ids": [str(i) for i in estado.evento_ids],
        "question": estado.pregunta,
        "correction_rounds": estado.rondas,
        "source_message_id": estado.origen,
        "last_prompt": estado.ultimo_texto,
        "last_inbound_at": ultimo_entrante,
        "advisor_requested_at": estado.asesor_at,
        "version": conv.version + 1,
    }
    enviar = _puede_responder(org, msg, ultimo_entrante, ahora)
    # Si esta respuesta sí sale, la que quedó sin enviar ya no sirve (la conversación
    # siguió): se borra para que la tarea de alertas no la mande tarde.
    cambios |= ({"unsent_reply": None, "unsent_reply_at": None} if enviar
                else {"unsent_reply": texto, "unsent_reply_at": ahora})
    repos.conversations.update(org_id, conv.id, **cambios)
    if enviar:
        channel.send_text(org.whatsapp_address, producer.phone, texto)
    return resultado


def _puede_responder(org: Organization, msg: InboundMessage,
                     ultimo_entrante: dt.datetime | None, ahora: dt.datetime) -> bool:
    if not org.whatsapp_address:
        logger.error("conversacion org=%s sin número de WhatsApp: no se responde", org.id)
        return False
    if not dentro_de_ventana(ultimo_entrante, ahora):
        # PENDIENTE(tarea de alertas): fuera de la ventana de 24 horas WhatsApp solo
        # deja mandar plantillas aprobadas. La respuesta queda en
        # conversations.unsent_reply para que esa tarea la mande con plantilla.
        # Ver app/channels/ventana.py.
        logger.warning("conversacion msg=%s fuera de la ventana de 24 h: respuesta guardada "
                       "sin enviar", msg.id)
        return False
    return True


def fallar(uow: UnitOfWork, channel: Channel, textos: TextosConversacion,
           organization_id: uuid.UUID, message_id: uuid.UUID, detalle: str, *,
           ahora: dt.datetime | None = None) -> str:
    """Deja el mensaje en "error" y le avisa al productor. La conversación queda
    como estaba (puede volver a mandar el mensaje)."""
    ahora = ahora or dt.datetime.now(dt.UTC)
    with uow() as repos:
        msg = repos.messages.get(organization_id, message_id)
        if msg is None or msg.producer_id is None or msg.processing_status != _estado_listo(msg):
            return "omitido"
        if repos.messages.set_processing_status(organization_id, message_id, "error",
                                                error_detail=detalle[:1000],
                                                expected=msg.processing_status) is None:
            return "omitido"
        org = repos.organizations.get(organization_id)
        producer = repos.producers.get(organization_id, msg.producer_id)
        if org is None or producer is None:
            return "error"
        conv = repos.conversations.get_or_create(organization_id, producer.id)
        ultimo = max(t for t in (conv.last_inbound_at, msg.received_at) if t)
        texto = textos.t("error")
        if _puede_responder(org, msg, ultimo, ahora):
            channel.send_text(org.whatsapp_address, producer.phone, texto)
        else:
            repos.conversations.update(organization_id, conv.id, unsent_reply=texto,
                                       unsent_reply_at=ahora)
    return "error"


# ---------------------------------------------------------------------------
# Acciones
# ---------------------------------------------------------------------------


def _ejecutar(ctx: _Ctx, estado: _Estado, accion: Accion, referencia: str | None,
              propuesta: list[EventoPropuesto] | None) -> tuple[str, str]:
    """Aplica la acción sobre `estado`. Devuelve (respuesta, resultado)."""
    t = ctx.textos.t

    if accion in ("vacio", "mis_lotes", "ayuda", "resumen"):
        texto = _comando(ctx, accion, referencia)
        if estado.estado != "libre" and estado.ultimo_texto:
            recordatorio = t("recordatorio", pregunta=estado.ultimo_texto)
            texto = f"{texto}\n\n{recordatorio}" if texto else recordatorio
        return texto or t("solo_audio_texto"), accion

    if accion == "nada_pendiente":
        return t("nada_pendiente"), accion

    if accion == "confirmar":
        # Regla de oro 2: el SÍ explícito del productor, después de ver el resumen.
        for evento_id in estado.evento_ids:
            _cambiar_evento(ctx, evento_id, ctx.repos.events.confirm)
        estado.limpiar()
        return t("confirmado"), "confirmado"

    if accion == "que_cambiar":
        return t("que_cambiar"), accion

    if accion == "cancelar":
        for evento_id in estado.evento_ids:
            _cambiar_evento(ctx, evento_id, ctx.repos.events.reject)
        estado.limpiar()
        return t("cancelado"), "cancelado"

    if accion == "asesor":
        return _derivar(ctx, estado)

    if accion == "crear_lote":
        return _crear_lote(ctx, estado)

    if accion == "rechazar_lote":
        b = estado.borradores[estado.pregunta["indice"]] if estado.pregunta else None
        if b is not None and b.lote_mencionado:
            estado.pregunta = {**(estado.pregunta or {}),
                               "lote_descartado": normalizar(b.lote_mencionado)}
        return _avanzar(ctx, estado)

    if accion == "extraer":
        if not propuesta:
            return t("no_entendi"), "no_entendido"
        estado.limpiar()
        estado.borradores, estado.origen = propuesta, ctx.msg.id
        return _avanzar(ctx, estado)

    # revisar
    if estado.estado == "confirmando":
        estado.rondas += 1
        if not propuesta or _sin_cambios(propuesta, estado.borradores):
            return t("no_entendi_correccion"), "no_entendido"
        estado.borradores = propuesta
        texto, resultado = _avanzar(ctx, estado)
        return texto, "corregido" if resultado == "resumen" else resultado
    if propuesta:
        estado.borradores = propuesta
    return _avanzar(ctx, estado)


def _cambiar_evento(ctx: _Ctx, evento_id: uuid.UUID,
                    cambio: Callable[[uuid.UUID, uuid.UUID], LotEvent | None]) -> None:
    try:
        cambio(ctx.org_id, evento_id)
    except InvalidTransitionError:
        # Alguien ya lo cambió desde el panel; se deja como está.
        logger.warning("conversacion evento=%s ya no estaba pendiente", evento_id)


def _clave(b: EventoPropuesto) -> tuple:
    datos = tuple(sorted((c, d.valor, d.unidad) for c, d in b.datos.items() if d is not None))
    return b.tipo_evento, b.codigo_lote, b.fecha, b.hora, datos


def _sin_cambios(nuevos: list[EventoPropuesto], viejos: list[EventoPropuesto]) -> bool:
    return [_clave(b) for b in nuevos] == [_clave(b) for b in viejos]


def _derivar(ctx: _Ctx, estado: _Estado) -> tuple[str, str]:
    """Demasiadas vueltas: se le pide hablar con el asesor. Los eventos ya creados
    quedan "pendiente" (no se confirman ni se rechazan) para que el asesor los
    revise en el panel."""
    estado.limpiar()
    estado.asesor_at = ctx.ahora
    return ctx.textos.t("asesor"), "asesor"


def _nombre_lote(ctx: _Ctx, codigo: str | None) -> str | None:
    if not codigo:
        return None
    lote = ctx.repos.lots.get_by_code(ctx.org_id, codigo)
    return lote.name if lote else codigo


def _pregunta_para(ctx: _Ctx, b: EventoPropuesto, campo: str) -> str:
    if campo in (FALTANTE_LOTE, FALTANTE_FECHA):
        return ctx.textos.preguntas[campo]
    tipo = ctx.catalogo.tipos[b.tipo_evento]
    definicion = tipo.campos.get(campo)
    if definicion and definicion.pregunta:
        return definicion.pregunta
    return ctx.textos.preguntas["generica"].replace("{campo}", campo.replace("_", " "))


def _avanzar(ctx: _Ctx, estado: _Estado) -> tuple[str, str]:
    """Pregunta lo primero que falte (un solo dato) o, si no falta nada, crea o
    actualiza los eventos pendientes y manda el resumen."""
    t = ctx.textos.t
    for i, b in enumerate(estado.borradores):
        if not b.faltantes:
            continue
        campo = b.faltantes[0]
        previa = estado.pregunta or {}
        misma = previa.get("indice") == i and previa.get("campo") == campo
        intentos = previa.get("intentos", 0) + 1 if misma else 1
        if intentos > ctx.textos.max_rondas:
            return _derivar(ctx, estado)
        descartado = previa.get("lote_descartado") if misma else None
        pregunta = {"indice": i, "campo": campo, "intentos": intentos,
                    "lote_descartado": descartado}

        if (campo == FALTANTE_LOTE and b.lote_mencionado
                and normalizar(b.lote_mencionado) != descartado):
            nombre = nombre_lote_nuevo(b.lote_mencionado)
            estado.estado, estado.pregunta = "ofreciendo_lote", pregunta | {"lote": nombre}
            texto = t("ofrecer_lote", lote=nombre)
        else:
            tipo = ctx.catalogo.tipos[b.tipo_evento]
            lote = _nombre_lote(ctx, b.codigo_lote)
            etiqueta = f"{tipo.nombre} en {lote}" if lote else tipo.nombre
            estado.estado, estado.pregunta = "preguntando", pregunta
            texto = t("pregunta_dato", evento=etiqueta, pregunta=_pregunta_para(ctx, b, campo))
        estado.ultimo_texto = texto
        return texto, "pregunta"

    _sincronizar_eventos(ctx, estado)
    lineas = [linea_evento(b.tipo_evento, _nombre_lote(ctx, b.codigo_lote), b.fecha, b.hora,
                           b.datos, ctx.hoy, ctx.textos, ctx.catalogo)
              for b in estado.borradores]
    if len(lineas) == 1:
        texto = t("confirmar_uno", evento=lineas[0])
    else:
        texto = t("confirmar_varios",
                  eventos="\n".join(f"{n}. {l}" for n, l in enumerate(lineas, start=1)))
    estado.estado, estado.pregunta, estado.ultimo_texto = "confirmando", None, texto
    return texto, "resumen"


def _momento(b: EventoPropuesto) -> dt.datetime:
    assert b.fecha is not None  # la fecha es obligatoria: no llega aquí sin ella
    return dt.datetime.combine(b.fecha, b.hora or dt.time(0), tzinfo=ZONA_COLOMBIA)


def _datos_json(b: EventoPropuesto) -> dict[str, Any]:
    """lot_events.data: {campo: {valor, unidad}}, solo lo que el productor dijo."""
    return {c: d.model_dump() for c, d in b.datos.items() if d is not None}


def _sincronizar_eventos(ctx: _Ctx, estado: _Estado) -> None:
    """Deja un evento "pendiente" por borrador, en el mismo orden.

    Una corrección actualiza el evento pendiente que ya existía (no crea otro). Si
    la corrección quitó eventos, los que sobran quedan rechazados (el productor
    dijo que no eran así).
    """
    repos, org_id = ctx.repos, ctx.org_id
    viejos = list(estado.evento_ids)
    nuevos: list[uuid.UUID] = []
    for i, b in enumerate(estado.borradores):
        lote = repos.lots.get_by_code(org_id, b.codigo_lote) if b.codigo_lote else None
        campos = {
            "lot_id": lote.id if lote else None,
            "event_type": b.tipo_evento,
            "occurred_at": _momento(b),
            "data": _datos_json(b),
            "original_text": b.fragmento or None,
        }
        ev = repos.events.get(org_id, viejos[i]) if i < len(viejos) else None
        if ev is not None and ev.status == "pendiente":
            repos.events.update(org_id, ev.id, **campos)
            nuevos.append(ev.id)
        else:
            nuevos.append(repos.events.add(org_id, message_id=estado.origen, **campos).id)
    for sobra in viejos[len(estado.borradores):]:
        _cambiar_evento(ctx, sobra, repos.events.reject)
    estado.evento_ids = nuevos


def _crear_lote(ctx: _Ctx, estado: _Estado) -> tuple[str, str]:
    """El productor dijo SÍ a crear el lote que nombró."""
    pregunta = estado.pregunta or {}
    i = pregunta.get("indice")
    if i is None or i >= len(estado.borradores) or not pregunta.get("lote"):
        return _avanzar(ctx, estado)
    repos, org_id = ctx.repos, ctx.org_id
    nombre = pregunta["lote"]

    farm_id = ctx.producer.farm_id
    if farm_id is None:
        fincas = repos.farms.list(org_id, limit=2)
        if len(fincas) != 1:
            # Sin saber de qué finca es, el lote lo tiene que crear el asesor.
            logger.warning("conversacion productor=%s sin finca: no se puede crear el lote",
                           ctx.producer.id)
            return _derivar(ctx, estado)
        farm_id = fincas[0].id

    lote = buscar_lote(nombre, repos.lots.list_active(org_id))
    if lote is None or normalizar(lote.name) != normalizar(nombre):
        lote = repos.lots.add(org_id, farm_id=farm_id, name=nombre,
                              code=_codigo_libre(repos, org_id, nombre))
        logger.info("conversacion productor=%s creó el lote %s", ctx.producer.id, lote.code)

    b = estado.borradores[i]
    estado.borradores[i] = b.model_copy(update={
        "codigo_lote": lote.code,
        "faltantes": [f for f in b.faltantes if f != FALTANTE_LOTE],
    })
    estado.pregunta = None
    texto, resultado = _avanzar(ctx, estado)
    return f"{ctx.textos.t('lote_creado', lote=lote.name)}\n{texto}", resultado


def _codigo_libre(repos: Repositories, org_id: uuid.UUID, nombre: str) -> str:
    """LOTE-003 para "Lote 3" si está libre; si no, el siguiente consecutivo libre."""
    numero = _numero_final(nombre) if nombre.startswith("Lote ") else None
    if numero is not None and repos.lots.get_by_code(org_id, f"LOTE-{numero:03d}") is None:
        return f"LOTE-{numero:03d}"
    n = len(repos.lots.list(org_id, limit=500)) + 1
    while repos.lots.get_by_code(org_id, f"LOTE-{n:03d}") is not None:
        n += 1
    return f"LOTE-{n:03d}"


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def _lotes_del_productor(ctx: _Ctx) -> list[Lot]:
    lotes = list(ctx.repos.lots.list_active(ctx.org_id))
    if ctx.producer.farm_id is None:
        return lotes
    return [l for l in lotes if l.farm_id == ctx.producer.farm_id]


def _comando(ctx: _Ctx, accion: Accion, referencia: str | None) -> str | None:
    t = ctx.textos.t
    if accion == "vacio":
        return None
    if accion == "ayuda":
        return t("ayuda")
    lotes = _lotes_del_productor(ctx)
    if accion == "mis_lotes":
        if not lotes:
            return t("sin_lotes")
        return t("mis_lotes", lotes="\n".join(f"• {l.name} ({l.code})" for l in lotes))

    # resumen
    if not referencia:
        return t("resumen_sin_lote")
    lote = buscar_lote(referencia, lotes)
    if lote is None:
        return t("lote_no_encontrado", lote=referencia)
    eventos = ctx.repos.events.list_by_lot(ctx.org_id, lote.id, status="confirmado")
    if not eventos:
        return t("resumen_vacio", lote=lote.name)
    lineas = []
    for ev in reversed(eventos[-RESUMEN_MAX_EVENTOS:]):  # lo más reciente primero
        local = a_hora_colombia(ev.occurred_at)
        hora = local.time() if (local.hour, local.minute) != (0, 0) else None
        lineas.append("• " + linea_evento(ev.event_type, None, local.date(), hora,
                                          _datos_desde_json(ev.data), ctx.hoy, ctx.textos,
                                          ctx.catalogo))
    return t("resumen", lote=f"{lote.name} ({lote.code})", eventos="\n".join(lineas))


def _datos_desde_json(data: dict[str, Any] | None) -> dict[str, DatoMedido | None]:
    """Tolera eventos guardados con otra forma (D-010: los reportes deben tolerarlo)."""
    datos: dict[str, DatoMedido | None] = {}
    for campo, valor in (data or {}).items():
        try:
            datos[campo] = (DatoMedido.model_validate(valor) if isinstance(valor, dict)
                            else DatoMedido(valor=valor))
        except ValueError:
            continue
    return datos
