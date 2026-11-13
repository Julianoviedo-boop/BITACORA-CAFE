"""Pruebas de extremo a extremo de la conversación con el canal falso.

Cada mensaje hace el camino real:

    webhook (_procesar) -> procesar_mensaje (consentimiento) -> [transcribir_audio]
      -> atender_conversacion -> canal falso

Las tareas de Celery corren en el mismo proceso (task_always_eager) contra
PostgreSQL real. Lo único falso es:
- el canal (no manda nada a WhatsApp: guarda lo que se envía),
- whisper (devuelve el texto que la prueba le diga), y
- el cliente HTTP de Anthropic: devuelve salidas guionadas. El prompt, el
  esquema y la normalización de la extracción sí corren.
"""

import datetime as dt
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.api.webhooks import _procesar
from app.channels.fake import FakeChannel
from app.db.models import Conversation, LotEvent
from app.db.repositories import Repositories, unit_of_work
from app.domain import conversacion, transcripcion
from app.domain.consentimiento import cargar_textos as cargar_textos_consentimiento
from app.extraction.llm import ClienteLLM
from app.extraction.prompts import a_hora_colombia
from app.extraction.schema import cargar_catalogo
from app.transcription.whisper import Transcripcion
from app.worker import tasks
from app.worker.celery_app import celery_app

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).parent / "fixtures"
AUDIO = "sintetico_01_abonada.ogg"
TELEFONO = "+573101234588"
_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]
_SIM = [Path(__file__).resolve().parents[2] / "scripts" / "simular_mensaje.py",
        Path("/scripts/simular_mensaje.py")]

CONFIRMAR = "¿Está bien? Responde SÍ o dime qué cambiar."


def _config_dir() -> Path:
    d = next((p for p in _CONFIG if (p / "conversacion.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/conversacion.yaml")
    return d


@pytest.fixture(scope="module")
def textos():
    return conversacion.cargar_textos(_config_dir())


@pytest.fixture(scope="module")
def catalogo():
    return cargar_catalogo(_config_dir())


@pytest.fixture(scope="module")
def sim():
    path = next((p for p in _SIM if p.is_file()), None)
    if path is None:
        pytest.skip("No se encontró scripts/simular_mensaje.py")
    spec = importlib.util.spec_from_file_location("simular_mensaje", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hoy() -> dt.date:
    return a_hora_colombia(dt.datetime.now(dt.UTC)).date()


# ---------------------------------------------------------------------------
# Claude falso
# ---------------------------------------------------------------------------


class AnthropicFalso:
    """Imita client.messages.parse: devuelve, en orden, las salidas que se le den."""

    def __init__(self) -> None:
        self.salidas: list[dict[str, Any]] = []
        self.llamadas: list[dict[str, Any]] = []
        self.messages = self

    def responde(self, *eventos: dict[str, Any]) -> None:
        self.salidas.append({"eventos": list(eventos)})

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.llamadas.append(kwargs)
        assert self.salidas, "Se llamó a Claude sin una salida preparada"
        salida = self.salidas.pop(0)
        uso = SimpleNamespace(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0,
                              cache_read_input_tokens=0)
        return SimpleNamespace(model="falso", stop_reason="end_turn", usage=uso,
                               parsed_output=kwargs["output_format"].model_validate(salida))

    @property
    def ultimo_mensaje(self) -> str:
        return self.llamadas[-1]["messages"][0]["content"]


def evento(tipo: str, *, lote: str | None = None, mencion: str | None = None,
           fecha: dt.date | None = None, hora: str | None = None,
           valores: tuple[tuple[str, Any, str | None], ...] = (),
           fragmento: str = "texto") -> dict[str, Any]:
    return {
        "tipo_evento": tipo, "fragmento": fragmento, "lote_mencionado": mencion,
        "codigo_lote": lote, "fecha": fecha.isoformat() if fecha else None, "hora": hora,
        "valores": [{"campo": c, "valor": v, "unidad": u} for c, v, u in valores],
        "faltantes": [],  # se recalculan al normalizar
    }


def abonada(cantidad: float = 4, **kw: Any) -> dict[str, Any]:
    return evento("abonada", lote="LOTE-003", mencion="el lote 3", fecha=hoy(), hora="07:30:00",
                  valores=(("insumo", "17-6-18", None), ("cantidad", cantidad, "bulto")), **kw)


# ---------------------------------------------------------------------------
# Mundo: organización, productor con autorización y dos lotes
# ---------------------------------------------------------------------------


class Productor:
    """Manda mensajes por el webhook y devuelve lo que el sistema le respondió."""

    def __init__(self, sim, canal: FakeChannel, numero: str, uow, transcriptor) -> None:
        self.sim, self.canal, self.numero, self.uow = sim, canal, numero, uow
        self.transcriptor = transcriptor

    def _enviar(self, **kw: Any) -> list[str]:
        antes = len(self.canal.sent)
        payload = self.sim.construir_payload(telefono=TELEFONO, numero=self.numero, **kw)
        _procesar(payload, self.canal, self.uow,
                  lambda org, msg: tasks.procesar_mensaje.delay(str(org), str(msg)))
        return [s.text for s in self.canal.sent[antes:]]

    def escribe(self, texto: str) -> list[str]:
        return self._enviar(texto=texto)

    def habla(self, transcripcion_: str) -> list[str]:
        self.transcriptor.texto = transcripcion_
        return self._enviar(audio=AUDIO)


class TranscriptorFalso:
    texto = ""

    def __call__(self, path: Path, max_duracion_s: float) -> Transcripcion:
        assert path.is_file()
        return Transcripcion(self.texto, 6.0, 0.8 if self.texto else 0.0)


@pytest.fixture
def repos(db_session) -> Repositories:
    return Repositories(db_session)


@pytest.fixture
def mundo(db_session, repos, monkeypatch, textos, catalogo, sim):
    config_dir = _config_dir()
    org_id = uuid.uuid4()
    numero = str(uuid.uuid4().int)[:15]
    repos.organizations.create(org_id, name="Asocafé Huila", slug=f"t-{org_id.hex[:8]}",
                               whatsapp_address=numero)
    finca = repos.farms.add(org_id, name="El Recreo", municipality="Pitalito")
    productor = repos.producers.add(org_id, full_name="Rosa Elena Muñoz", phone=TELEFONO,
                                    farm_id=finca.id)
    repos.producers.record_consent(org_id, productor.id, "aceptado")
    repos.lots.add(org_id, farm_id=finca.id, code="LOTE-001", name="La Esperanza")
    repos.lots.add(org_id, farm_id=finca.id, code="LOTE-003", name="Lote 3")
    db_session.flush()

    canal = FakeChannel(FIXTURES)
    claude = AnthropicFalso()
    transcriptor = TranscriptorFalso()
    uow = lambda: unit_of_work(db_session)  # noqa: E731

    monkeypatch.setattr(tasks, "unit_of_work", uow)
    monkeypatch.setattr(tasks, "get_channel", lambda: canal)
    monkeypatch.setattr(tasks, "get_textos", lambda: cargar_textos_consentimiento(config_dir))
    monkeypatch.setattr(tasks, "_transcriptor", transcriptor)
    monkeypatch.setattr(tasks, "_interprete", lambda: conversacion.Interprete(
        catalogo=catalogo, cliente=ClienteLLM(client=claude, dormir=lambda s: None)))
    monkeypatch.setattr(transcripcion, "get_reglas",
                        lambda: transcripcion.cargar_reglas(config_dir))
    monkeypatch.setattr(conversacion, "get_textos", lambda: textos)
    for clave, valor in (("task_always_eager", True), ("task_eager_propagates", True)):
        monkeypatch.setitem(celery_app.conf, clave, valor)

    return SimpleNamespace(
        org_id=org_id, productor_id=productor.id, finca_id=finca.id, canal=canal,
        claude=claude, repos=repos, session=db_session, uow=uow, catalogo=catalogo,
        textos=textos, productor=Productor(sim, canal, numero, uow, transcriptor),
    )


def _eventos(m) -> list[LotEvent]:
    m.session.expire_all()
    stmt = (select(LotEvent).where(LotEvent.organization_id == m.org_id)
            .order_by(LotEvent.created_at, LotEvent.id))
    return list(m.session.scalars(stmt))


def _conversacion(m) -> Conversation:
    m.session.expire_all()
    return m.repos.conversations.get_by_producer(m.org_id, m.productor_id)


def _estados_mensajes(m) -> list[str]:
    m.session.expire_all()
    return [x.processing_status for x in m.repos.messages.list(m.org_id)]


# ---------------------------------------------------------------------------
# Los cinco caminos pedidos
# ---------------------------------------------------------------------------


def test_feliz_audio_resumen_y_si(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())

    respuesta = p.habla("Hoy a las siete y media abonamos el lote 3 con 4 bultos de 17-6-18")
    assert respuesta == [f"Lote 3: abonada, hoy 7:30 a.m., 17-6-18, 4 bultos. {CONFIRMAR}"]
    # La extracción recibió la transcripción, no el audio.
    assert "abonamos el lote 3" in claude.ultimo_mensaje

    [ev] = _eventos(mundo)
    assert ev.status == "pendiente" and ev.confirmed_at is None
    assert ev.event_type == "abonada" and ev.lot_id is not None
    assert ev.data == {"insumo": {"valor": "17-6-18", "unidad": None},
                       "cantidad": {"valor": 4, "unidad": "bulto"}}
    assert a_hora_colombia(ev.occurred_at).time() == dt.time(7, 30)
    assert _conversacion(mundo).state == "confirmando"

    assert p.escribe("Sí") == ["Listo ✅ Quedó guardado en la historia del lote."]
    [ev] = _eventos(mundo)
    assert ev.status == "confirmado" and ev.confirmed_at is not None
    conv = _conversacion(mundo)
    assert (conv.state, conv.drafts, conv.event_ids, conv.correction_rounds) == \
        ("libre", [], [], 0)
    assert _estados_mensajes(mundo) == ["procesado", "procesado"]
    assert len(claude.llamadas) == 1  # el SÍ no pasa por Claude


def test_correccion_por_audio_actualiza_el_pendiente(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    p.escribe("Hoy a las 7:30 abonamos el lote 3 con 4 bultos de 17-6-18")
    [original_id] = [e.id for e in _eventos(mundo)]

    claude.responde(abonada(cantidad=5))
    assert p.habla("No, fueron cinco bultos") == \
        [f"Lote 3: abonada, hoy 7:30 a.m., 17-6-18, 5 bultos. {CONFIRMAR}"]
    # Claude recibió los eventos propuestos, el resumen que vio y la corrección.
    mensaje = claude.ultimo_mensaje
    assert "<eventos_propuestos>" in mensaje and '"valor": 4' in mensaje
    assert "<pregunta_al_productor>" in mensaje and "¿Está bien?" in mensaje
    assert "No, fueron cinco bultos" in mensaje

    [corregido] = _eventos(mundo)  # el mismo evento, no uno nuevo
    assert corregido.id == original_id and corregido.status == "pendiente"
    assert corregido.data["cantidad"] == {"valor": 5, "unidad": "bulto"}
    assert _conversacion(mundo).correction_rounds == 1

    p.escribe("si")
    [ev] = _eventos(mundo)
    assert ev.status == "confirmado" and ev.data["cantidad"]["valor"] == 5


def test_cancelacion_no_guarda_nada(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    p.escribe("Hoy abonamos el lote 3 con 4 bultos de 17-6-18")

    assert p.escribe("Cancelar") == \
        ["Listo, no guardé nada. Cuando quieras me vuelves a contar."]
    [ev] = _eventos(mundo)
    assert ev.status == "rechazado" and ev.confirmed_at is None
    assert _conversacion(mundo).state == "libre"
    # Ya no hay nada que confirmar.
    assert p.escribe("sí") == [mundo.textos.t("nada_pendiente")]
    assert _eventos(mundo)[0].status == "rechazado"


def test_dato_faltante_se_pregunta_de_a_uno(mundo):
    p, claude = mundo.productor, mundo.claude
    sin_datos = evento("abonada", lote="LOTE-003", mencion="el 3", fecha=hoy())
    claude.responde(sin_datos)

    assert p.escribe("Hoy abonamos el 3") == \
        ["Abonada en Lote 3: ¿Qué abono echaste? Por ejemplo, 17-6-18, DAP o urea."]
    assert _eventos(mundo) == []  # no se crea el evento mientras falten datos
    conv = _conversacion(mundo)
    assert conv.state == "preguntando" and conv.question["campo"] == "insumo"

    claude.responde({**sin_datos, "valores": [{"campo": "insumo", "valor": "DAP",
                                               "unidad": None}]})
    assert p.escribe("DAP") == \
        ["Abonada en Lote 3: ¿Cuánto abono echaste en total? Por ejemplo, 4 bultos."]
    # Claude supo qué se le había preguntado.
    assert "¿Qué abono echaste?" in claude.ultimo_mensaje

    claude.responde({**sin_datos, "valores": [
        {"campo": "insumo", "valor": "DAP", "unidad": None},
        {"campo": "cantidad", "valor": 2, "unidad": "bulto"}]})
    assert p.habla("dos bultos") == [f"Lote 3: abonada, hoy, DAP, 2 bultos. {CONFIRMAR}"]
    assert [e.status for e in _eventos(mundo)] == ["pendiente"]

    p.escribe("Sí señor")
    assert [e.status for e in _eventos(mundo)] == ["confirmado"]


def test_audio_incomprensible(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde()  # Claude no encuentra ninguna labor

    assert p.habla("eh... este... mmm... ¿sí me escucha?") == [mundo.textos.t("no_entendi")]
    assert _eventos(mundo) == []
    assert _conversacion(mundo).state == "libre"
    assert _estados_mensajes(mundo) == ["procesado"]


def test_audio_sin_voz_avisa_sin_llamar_a_claude(mundo):
    respuesta = mundo.productor.habla("")
    assert respuesta == [transcripcion.cargar_reglas(_config_dir()).aviso("audio_sin_voz")]
    assert mundo.claude.llamadas == [] and _eventos(mundo) == []


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------


def _fumigacion_lote_7() -> dict[str, Any]:
    return evento("fumigacion", mencion="el lote 7", fecha=hoy() - dt.timedelta(days=1),
                  valores=(("producto", "Verdadero", None),))


def test_lote_que_no_existe_se_ofrece_crear(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(_fumigacion_lote_7())
    assert p.escribe("Ayer fumigamos el lote 7 con Verdadero") == [
        "No encontré el lote «Lote 7» en tu lista. ¿Quieres que lo cree? Responde SÍ, o dime "
        "el nombre o el número del lote."]
    assert _conversacion(mundo).state == "ofreciendo_lote"

    assert p.escribe("Sí") == [
        f"Listo, creé el lote «Lote 7».\nLote 7: fumigación, ayer, Verdadero. {CONFIRMAR}"]
    lote = mundo.repos.lots.get_by_code(mundo.org_id, "LOTE-007")
    assert lote is not None and lote.name == "Lote 7" and lote.farm_id == mundo.finca_id
    [ev] = _eventos(mundo)
    assert ev.lot_id == lote.id and ev.status == "pendiente"

    p.escribe("listo")
    assert _eventos(mundo)[0].status == "confirmado"
    assert len(claude.llamadas) == 1


def test_lote_que_no_existe_y_no_quiere_crearlo(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(_fumigacion_lote_7())
    p.escribe("Ayer fumigamos el lote 7 con Verdadero")

    assert p.escribe("No") == [
        "Fumigación: ¿En qué lote fue? Dime el nombre o el número."]
    assert mundo.repos.lots.get_by_code(mundo.org_id, "LOTE-007") is None

    claude.responde({**_fumigacion_lote_7(), "codigo_lote": "LOTE-001",
                     "lote_mencionado": "La Esperanza"})
    assert p.escribe("Fue en La Esperanza") == \
        [f"La Esperanza: fumigación, ayer, Verdadero. {CONFIRMAR}"]


def test_venta_sin_lote_queda_sin_lote(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(evento("venta", fecha=hoy(), valores=(("cantidad", 2, "carga"),
                                                         ("precio", 2_150_000, "peso_por_carga"))))
    assert p.escribe("Hoy vendí 2 cargas a 2.150.000") == \
        [f"Venta, hoy, 2 cargas, 2.150.000 pesos la carga. {CONFIRMAR}"]
    p.escribe("sí")
    [ev] = _eventos(mundo)
    assert ev.lot_id is None and ev.status == "confirmado"


# ---------------------------------------------------------------------------
# Límites: rondas de corrección y preguntas repetidas
# ---------------------------------------------------------------------------


def test_despues_de_tres_correcciones_pide_el_asesor(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    p.escribe("Hoy abonamos el lote 3 con 4 bultos de 17-6-18")
    for cantidad in (5, 6, 7):
        claude.responde(abonada(cantidad=cantidad))
        assert "¿Está bien?" in p.escribe(f"no, fueron {cantidad}")[0]

    assert p.escribe("no, fueron 8") == [mundo.textos.t("asesor")]
    assert len(claude.llamadas) == 4  # la cuarta corrección ya no va a Claude
    [ev] = _eventos(mundo)
    # Queda pendiente para que el asesor lo revise: ni confirmado ni rechazado.
    assert ev.status == "pendiente" and ev.data["cantidad"]["valor"] == 7
    conv = _conversacion(mundo)
    assert conv.state == "libre" and conv.advisor_requested_at is not None
    # Un SÍ después ya no confirma nada.
    p.escribe("sí")
    assert _eventos(mundo)[0].status == "pendiente"


def test_no_es_una_correccion_cuenta_como_ronda(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    p.escribe("Hoy abonamos el lote 3 con 4 bultos de 17-6-18")
    claude.responde(abonada())  # Claude no encontró nada que cambiar
    assert p.escribe("pues no sé") == [mundo.textos.t("no_entendi_correccion")]
    assert _conversacion(mundo).correction_rounds == 1


def test_no_a_secas_pregunta_que_cambiar(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    p.escribe("Hoy abonamos el lote 3 con 4 bultos de 17-6-18")
    assert p.escribe("No") == [mundo.textos.t("que_cambiar")]
    assert len(claude.llamadas) == 1 and _conversacion(mundo).correction_rounds == 0


def test_misma_pregunta_tres_veces_pide_el_asesor(mundo):
    p, claude = mundo.productor, mundo.claude
    sin_datos = evento("abonada", lote="LOTE-003", fecha=hoy(),
                       valores=(("cantidad", 4, "bulto"),))
    claude.responde(sin_datos)
    p.escribe("Hoy abonamos el lote 3, 4 bultos")
    for _ in range(2):
        claude.responde(sin_datos)
        assert "¿Qué abono echaste?" in p.escribe("no me acuerdo")[0]
    claude.responde(sin_datos)
    assert p.escribe("ni idea") == [mundo.textos.t("asesor")]
    assert _eventos(mundo) == []


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def test_comandos(mundo):
    p = mundo.productor
    assert p.escribe("Mis lotes") == ["Tus lotes:\n• La Esperanza (LOTE-001)\n• Lote 3 (LOTE-003)"]
    assert p.escribe("AYUDA") == [mundo.textos.t("ayuda")]
    assert p.escribe("resumen lote 3") == ["Lote 3 todavía no tiene registros confirmados."]
    assert p.escribe("resumen lote 9") == [
        "No encontré el lote «9». Escribe MIS LOTES para ver tus lotes."]
    assert mundo.claude.llamadas == []


def test_resumen_lote_muestra_lo_confirmado(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    p.escribe("Hoy abonamos el lote 3 con 4 bultos de 17-6-18")
    p.escribe("sí")
    assert p.escribe("Resumen del lote 3") == [
        "Lote 3 (LOTE-003), últimos registros:\n• Abonada, hoy 7:30 a.m., 17-6-18, 4 bultos"]


def test_comando_en_medio_de_la_confirmacion_no_pierde_el_estado(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    [resumen] = p.escribe("Hoy abonamos el lote 3 con 4 bultos de 17-6-18")

    [respuesta] = p.escribe("mis lotes")
    assert respuesta.startswith("Tus lotes:")
    assert respuesta.endswith(f"Sigo esperando tu respuesta: {resumen}")
    assert _conversacion(mundo).state == "confirmando"
    p.escribe("sí")
    assert _eventos(mundo)[0].status == "confirmado"


def test_resumen_de_la_semana_no_es_un_comando(mundo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada(), evento("desyerba", lote="LOTE-001", fecha=hoy(),
                                      valores=(("metodo", "guadaña", None),)))
    assert p.escribe("Resumen de la semana: hoy abonamos el lote 3 con 4 bultos de 17-6-18 y "
                     "guadañamos La Esperanza") == [
        "Entendí esto:\n"
        "1. Lote 3: abonada, hoy 7:30 a.m., 17-6-18, 4 bultos\n"
        "2. La Esperanza: desyerba, hoy, guadaña\n"
        + CONFIRMAR]
    p.escribe("ok")
    assert [e.status for e in _eventos(mundo)] == ["confirmado", "confirmado"]


# ---------------------------------------------------------------------------
# Estado en la base, idempotencia, errores y ventana de 24 horas
# ---------------------------------------------------------------------------


def test_el_estado_sobrevive_a_un_reinicio_del_worker(mundo, monkeypatch, catalogo):
    p, claude = mundo.productor, mundo.claude
    claude.responde(abonada())
    p.escribe("Hoy abonamos el lote 3 con 4 bultos de 17-6-18")

    # "Reinicio": canal, Claude y textos nuevos; nada queda en memoria salvo la base.
    canal_nuevo, claude_nuevo = FakeChannel(FIXTURES), AnthropicFalso()
    monkeypatch.setattr(tasks, "get_channel", lambda: canal_nuevo)
    monkeypatch.setattr(tasks, "_interprete", lambda: conversacion.Interprete(
        catalogo=catalogo, cliente=ClienteLLM(client=claude_nuevo, dormir=lambda s: None)))
    monkeypatch.setattr(conversacion, "get_textos",
                        lambda: conversacion.cargar_textos(_config_dir()))
    p.canal = canal_nuevo

    assert p.escribe("Sí") == ["Listo ✅ Quedó guardado en la historia del lote."]
    assert _eventos(mundo)[0].status == "confirmado"
    assert claude_nuevo.llamadas == []


def _mensaje(m, texto: str) -> uuid.UUID:
    msg, _ = m.repos.messages.add_if_new(
        m.org_id, f"wamid.{uuid.uuid4().hex}", producer_id=m.productor_id, from_phone=TELEFONO,
        message_type="texto", text_body=texto)
    m.session.flush()
    return msg.id


def _interprete(m) -> conversacion.Interprete:
    return conversacion.Interprete(catalogo=m.catalogo,
                                   cliente=ClienteLLM(client=m.claude, dormir=lambda s: None))


def test_no_atiende_dos_veces_el_mismo_mensaje(mundo):
    mundo.claude.responde(abonada())
    msg_id = _mensaje(mundo, "Hoy abonamos el lote 3 con 4 bultos de 17-6-18")
    args = (mundo.uow, mundo.canal, _interprete(mundo), mundo.textos, mundo.org_id, msg_id)
    assert conversacion.atender(*args) == "resumen"
    assert conversacion.atender(*args) == "omitido"
    assert len(mundo.canal.sent) == 1 and len(_eventos(mundo)) == 1


def test_no_atiende_si_revoco_la_autorizacion(mundo):
    msg_id = _mensaje(mundo, "Hoy abonamos el lote 3")
    mundo.repos.producers.record_consent(mundo.org_id, mundo.productor_id, "revocado")
    assert conversacion.atender(mundo.uow, mundo.canal, _interprete(mundo), mundo.textos,
                                mundo.org_id, msg_id) == "omitido"
    assert mundo.canal.sent == [] and mundo.claude.llamadas == []


def test_no_atiende_mensajes_de_otra_organizacion(mundo):
    msg_id = _mensaje(mundo, "Hoy abonamos el lote 3")
    assert conversacion.atender(mundo.uow, mundo.canal, _interprete(mundo), mundo.textos,
                                uuid.uuid4(), msg_id) == "omitido"
    assert mundo.canal.sent == []


def test_fuera_de_la_ventana_de_24_horas_no_envia_y_lo_deja_marcado(mundo):
    mundo.claude.responde(abonada())
    msg_id = _mensaje(mundo, "Hoy abonamos el lote 3 con 4 bultos de 17-6-18")
    recibido = mundo.repos.messages.get(mundo.org_id, msg_id).received_at
    resultado = conversacion.atender(
        mundo.uow, mundo.canal, _interprete(mundo), mundo.textos, mundo.org_id, msg_id,
        ahora=recibido + dt.timedelta(hours=25))

    assert resultado == "resumen" and mundo.canal.sent == []
    conv = _conversacion(mundo)
    assert conv.unsent_reply.startswith("Lote 3: abonada") and conv.unsent_reply_at is not None
    # El estado sí avanzó: cuando el productor escriba, puede confirmar.
    assert conv.state == "confirmando"

    # Vuelve a escribir (ventana abierta): se le responde y lo no enviado se borra.
    assert mundo.productor.escribe("sí") == ["Listo ✅ Quedó guardado en la historia del lote."]
    conv = _conversacion(mundo)
    assert conv.unsent_reply is None and conv.unsent_reply_at is None
    assert _eventos(mundo)[0].status == "confirmado"


def test_error_temporal_de_claude_deja_el_mensaje_para_reintentar(mundo):
    import anthropic
    import httpx

    def caido(**kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))

    mundo.claude.parse = caido
    msg_id = _mensaje(mundo, "Hoy abonamos el lote 3")
    with pytest.raises(conversacion.ReintentarError):
        conversacion.atender(mundo.uow, mundo.canal, _interprete(mundo), mundo.textos,
                             mundo.org_id, msg_id)
    assert mundo.repos.messages.get(mundo.org_id, msg_id).processing_status == "recibido"
    assert mundo.canal.sent == []


def test_tarea_avisa_cuando_claude_sigue_caido(mundo, monkeypatch):
    import anthropic
    import httpx

    def caido(**kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))

    mundo.claude.parse = caido
    # Con task_eager_propagates el Retry se escapa en vez de volver a correr la tarea.
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)
    assert mundo.productor.escribe("Hoy abonamos el lote 3") == [mundo.textos.t("error")]
    assert _estados_mensajes(mundo) == ["error"]


def test_error_definitivo_de_claude_avisa(mundo):
    def se_niega(**kwargs):
        return SimpleNamespace(model="falso", stop_reason="refusal", parsed_output=None,
                               stop_details=None,
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    mundo.claude.parse = se_niega
    msg_id = _mensaje(mundo, "Hoy abonamos el lote 3")
    assert conversacion.atender(mundo.uow, mundo.canal, _interprete(mundo), mundo.textos,
                                mundo.org_id, msg_id) == "error"
    msg = mundo.repos.messages.get(mundo.org_id, msg_id)
    assert msg.processing_status == "error" and msg.error_detail.startswith("extraccion")
    assert [s.text for s in mundo.canal.sent] == [mundo.textos.t("error")]


def test_si_otro_worker_cambia_la_conversacion_se_vuelve_a_empezar(mundo):
    """Mientras se habla con Claude, otro mensaje del productor cambia la conversación:
    no se pisa su cambio, se vuelve a leer y a interpretar."""
    claude = mundo.claude
    claude.responde(abonada())
    claude.responde(abonada(cantidad=5))
    msg_id = _mensaje(mundo, "Hoy abonamos el lote 3 con 4 bultos de 17-6-18")
    parse_original = claude.parse

    def parse_con_otro_worker(**kwargs):
        if len(claude.llamadas) == 0:
            with mundo.uow() as repos:
                conv = repos.conversations.get_or_create(mundo.org_id, mundo.productor_id)
                repos.conversations.update(mundo.org_id, conv.id, version=conv.version + 1)
        return parse_original(**kwargs)

    claude.parse = parse_con_otro_worker
    assert conversacion.atender(mundo.uow, mundo.canal, _interprete(mundo), mundo.textos,
                                mundo.org_id, msg_id) == "resumen"
    assert len(claude.llamadas) == 2  # se interpretó otra vez con el estado nuevo
    assert len(mundo.canal.sent) == 1 and "5 bultos" in mundo.canal.sent[0].text
