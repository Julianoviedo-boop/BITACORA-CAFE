"""Caso de uso de transcripción (app/domain/transcripcion.py) y sus tareas de Celery.

El transcriptor es falso (no corre whisper); la descarga usa el canal falso con
los audios de tests/fixtures.
"""

import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.channels.base import ChannelTemporaryError
from app.channels.fake import FakeChannel
from app.db.repositories import Repositories, unit_of_work
from app.domain import transcripcion
from app.domain.consentimiento import atender_mensaje, cargar_textos
from app.domain.transcripcion import (
    ReglasTranscripcion,
    Resultado,
    archivo_temporal,
    cargar_reglas,
    enviar_aviso,
    registrar_fallo,
    transcribir_mensaje,
)
from app.transcription.whisper import (
    AudioDemasiadoLargoError,
    AudioInvalidoError,
    Transcripcion,
)

FIXTURES = Path(__file__).parent / "fixtures"
AUDIO = "fixture://sintetico_01_abonada.ogg"
TELEFONO = "+573101234577"
_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


def _config_dir() -> Path:
    d = next((p for p in _CONFIG if (p / "transcripcion.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/transcripcion.yaml")
    return d


@pytest.fixture(scope="module")
def reglas() -> ReglasTranscripcion:
    return cargar_reglas(_config_dir())


# ---------------------------------------------------------------------------
# Reglas (sin base)
# ---------------------------------------------------------------------------


def test_reglas_del_repositorio(reglas):
    assert reglas.max_duracion_s == 180
    largo = reglas.aviso("audio_muy_largo")
    assert "3 minutos" in largo and "{minutos}" not in largo
    for clave in transcripcion.CLAVES_AVISO:
        assert reglas.aviso(clave)


def test_minutos_con_decimales():
    r = ReglasTranscripcion(90, {"audio_muy_largo": "máximo {minutos} minutos"})
    assert r.aviso("audio_muy_largo") == "máximo 1,5 minutos"


@pytest.mark.parametrize("yaml_txt,error", [
    ("avisos: {}\n", "max_duracion_segundos"),
    ("max_duracion_segundos: tres\n", "max_duracion_segundos"),
    ("max_duracion_segundos: 0\n", "max_duracion_segundos"),
    ("max_duracion_segundos: 180\navisos:\n  audio_muy_largo: a\n", "audio_no_disponible"),
])
def test_reglas_invalidas(tmp_path, yaml_txt, error):
    (tmp_path / "transcripcion.yaml").write_text(yaml_txt, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        cargar_reglas(tmp_path)


# ---------------------------------------------------------------------------
# Archivo temporal (sin base)
# ---------------------------------------------------------------------------


def test_archivo_temporal_se_borra_al_terminar():
    with archivo_temporal(b"OggS", ".ogg") as p:
        assert p.read_bytes() == b"OggS" and p.suffix == ".ogg"
    assert not p.exists()


def test_archivo_temporal_se_borra_con_error():
    with pytest.raises(RuntimeError), archivo_temporal(b"x", ".ogg") as p:
        raise RuntimeError("falla en whisper")
    assert not p.exists()


def test_archivo_temporal_ya_borrado_no_falla():
    with archivo_temporal(b"x", ".ogg") as p:
        p.unlink()


# ---------------------------------------------------------------------------
# Caso de uso (con base de datos)
# ---------------------------------------------------------------------------


class Transcriptor:
    """Registra las rutas que recibe y devuelve (o lanza) lo que se le diga."""

    def __init__(self, resultado: Transcripcion | Exception | None = None) -> None:
        self.resultado = resultado or Transcripcion("Hoy abonamos el lote El Mirador", 10.3, 0.9,
                                                    {"decodificar": 0.1, "transcribir": 2.0})
        self.rutas: list[Path] = []
        self.maximos: list[float] = []

    def __call__(self, path: Path, max_duracion_s: float) -> Transcripcion:
        self.rutas.append(path)
        self.maximos.append(max_duracion_s)
        assert path.is_file() and path.read_bytes()[:4] == b"OggS"
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


@pytest.fixture
def repos(db_session) -> Repositories:
    return Repositories(db_session)


@pytest.fixture
def uow(db_session):
    return lambda: unit_of_work(db_session)


@pytest.fixture
def canal() -> FakeChannel:
    return FakeChannel(FIXTURES)


@pytest.fixture
def org(repos):
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name="Asocafé Huila", slug=f"t-{org_id.hex[:8]}",
                               whatsapp_address=str(uuid.uuid4().int)[:15])
    producer = repos.producers.add(org_id, full_name="Rosa Elena Muñoz", phone=TELEFONO)
    repos.producers.record_consent(org_id, producer.id, "aceptado")
    repos.session.flush()
    return {"id": org_id, "producer_id": producer.id}


def _audio(repos, org, media_ref=AUDIO) -> uuid.UUID:
    msg, _ = repos.messages.add_if_new(
        org["id"], f"wamid.{uuid.uuid4().hex}", producer_id=org["producer_id"],
        from_phone=TELEFONO, message_type="audio", media_ref=media_ref,
    )
    repos.session.flush()
    return msg.id


@pytest.mark.db
def test_transcribe_y_guarda(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org)
    t = Transcriptor()
    r = transcribir_mensaje(uow, canal, t, reglas, org["id"], msg_id)

    assert r == Resultado("transcrito")
    msg = repos.messages.get(org["id"], msg_id)
    assert msg.processing_status == "transcrito"
    assert msg.transcription == "Hoy abonamos el lote El Mirador"
    assert t.maximos == [180]
    assert t.rutas[0].suffix == ".ogg" and not t.rutas[0].exists()  # temporal borrado
    assert canal.sent == []


@pytest.mark.db
def test_audio_largo_avisa_y_borra_el_temporal(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org)
    t = Transcriptor(AudioDemasiadoLargoError(245, 180))
    r = transcribir_mensaje(uow, canal, t, reglas, org["id"], msg_id)

    assert r.estado == "fallido" and r.aviso == reglas.aviso("audio_muy_largo")
    msg = repos.messages.get(org["id"], msg_id)
    assert msg.processing_status == "error" and msg.error_detail.startswith("audio_muy_largo")
    assert msg.transcription is None
    assert not t.rutas[0].exists()


@pytest.mark.db
@pytest.mark.parametrize("error,clave", [
    (AudioInvalidoError("dañado"), "audio_no_disponible"),
    (RuntimeError("se cayó ctranslate2"), "error_transcripcion"),
    (MemoryError(), "error_transcripcion"),
])
def test_error_al_transcribir_avisa_y_borra_el_temporal(uow, repos, canal, reglas, org, error, clave):
    msg_id = _audio(repos, org)
    t = Transcriptor(error)
    r = transcribir_mensaje(uow, canal, t, reglas, org["id"], msg_id)

    assert r.estado == "fallido" and r.aviso == reglas.aviso(clave)
    assert repos.messages.get(org["id"], msg_id).processing_status == "error"
    assert not t.rutas[0].exists()


@pytest.mark.db
def test_sin_voz_avisa(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org)
    r = transcribir_mensaje(uow, canal, Transcriptor(Transcripcion("", 8.0, 0.0)), reglas,
                            org["id"], msg_id)
    assert r.estado == "fallido" and r.aviso == reglas.aviso("audio_sin_voz")


@pytest.mark.db
def test_audio_que_no_existe_avisa_sin_transcribir(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org, media_ref="fixture://no-existe.ogg")
    t = Transcriptor()
    r = transcribir_mensaje(uow, canal, t, reglas, org["id"], msg_id)
    assert r.estado == "fallido" and r.aviso == reglas.aviso("audio_no_disponible")
    assert t.rutas == []


@pytest.mark.db
def test_falla_temporal_de_descarga_sube_para_reintentar(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org)

    def caido(ref):
        raise ChannelTemporaryError("OpenBSP respondió 503")

    canal.download_media = caido
    with pytest.raises(ChannelTemporaryError):
        transcribir_mensaje(uow, canal, Transcriptor(), reglas, org["id"], msg_id)
    assert repos.messages.get(org["id"], msg_id).processing_status == "recibido"


@pytest.mark.db
def test_no_transcribe_si_revoco_el_consentimiento(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org)
    repos.producers.record_consent(org["id"], org["producer_id"], "revocado")
    canal.download_media = lambda ref: pytest.fail("No debía descargar")
    r = transcribir_mensaje(uow, canal, Transcriptor(), reglas, org["id"], msg_id)
    assert r == Resultado("omitido")


@pytest.mark.db
def test_no_transcribe_dos_veces(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org)
    assert transcribir_mensaje(uow, canal, Transcriptor(), reglas, org["id"], msg_id).estado == \
        "transcrito"
    canal.download_media = lambda ref: pytest.fail("No debía descargar")
    assert transcribir_mensaje(uow, canal, Transcriptor(), reglas, org["id"], msg_id) == \
        Resultado("omitido")


@pytest.mark.db
def test_no_transcribe_mensajes_de_otra_organizacion(uow, repos, canal, reglas, org):
    msg_id = _audio(repos, org)
    canal.download_media = lambda ref: pytest.fail("No debía descargar")
    assert transcribir_mensaje(uow, canal, Transcriptor(), reglas, uuid.uuid4(), msg_id) == \
        Resultado("omitido")


@pytest.mark.db
def test_fallo_no_se_registra_dos_veces(uow, repos, reglas, org):
    msg_id = _audio(repos, org)
    primero = registrar_fallo(uow, reglas, org["id"], msg_id, "audio_no_disponible", "x")
    segundo = registrar_fallo(uow, reglas, org["id"], msg_id, "audio_no_disponible", "x")
    assert primero.estado == "fallido" and segundo.estado == "omitido" and segundo.aviso is None


@pytest.mark.db
def test_enviar_aviso_al_productor(repos, canal, org):
    msg_id = _audio(repos, org)
    assert enviar_aviso(repos, canal, org["id"], msg_id, "Vuelva a mandarlo")
    numero = repos.organizations.get(org["id"]).whatsapp_address
    assert [(s.channel_address, s.to_phone, s.text) for s in canal.sent] == \
        [(numero, TELEFONO, "Vuelva a mandarlo")]
    assert not enviar_aviso(repos, canal, uuid.uuid4(), msg_id, "otro")


@pytest.mark.db
def test_audio_con_consentimiento_queda_listo_para_transcribir(repos, canal, org):
    config_dir = next((p for p in _CONFIG if (p / "consentimiento.yaml").is_file()), None)
    if config_dir is None:
        pytest.skip("No se encontró config/consentimiento.yaml")
    msg_id = _audio(repos, org)
    assert atender_mensaje(repos, canal, org["id"], msg_id, cargar_textos(config_dir)) == \
        "listo_para_transcribir"


# ---------------------------------------------------------------------------
# Tareas de Celery (sin base: se reemplaza el caso de uso)
# ---------------------------------------------------------------------------


@pytest.fixture
def tareas(monkeypatch, reglas):
    from app.worker import tasks

    avisos, conversaciones = [], []
    monkeypatch.setattr(tasks, "get_channel", lambda: "canal")
    monkeypatch.setattr(transcripcion, "get_reglas", lambda: reglas)
    monkeypatch.setattr(tasks.avisar_productor, "delay", lambda *a: avisos.append(a))
    monkeypatch.setattr(tasks.atender_conversacion, "delay", lambda *a: conversaciones.append(a))
    tasks.avisos = avisos
    tasks.conversaciones = conversaciones
    return tasks


def test_tarea_reintenta_la_descarga_y_al_final_avisa(monkeypatch, tareas):
    intentos, fallos = [], []

    def caido(*args):
        intentos.append(args)
        raise ChannelTemporaryError("503")

    def fallo(uow, reglas, org_id, msg_id, clave, detalle):
        fallos.append((clave, detalle))
        return Resultado("fallido", aviso="No pudimos abrir su audio", detalle=clave)

    monkeypatch.setattr(transcripcion, "transcribir_mensaje", caido)
    monkeypatch.setattr(transcripcion, "registrar_fallo", fallo)
    org, msg = str(uuid.uuid4()), str(uuid.uuid4())

    # apply() corre en el mismo proceso y ejecuta los reintentos de una vez.
    assert tareas.transcribir_audio.apply(args=(org, msg)).get() == "fallido"
    assert len(intentos) == tareas.DESCARGA_MAX_REINTENTOS + 1
    assert fallos == [("audio_no_disponible",
                       f"Descarga fallida tras {tareas.DESCARGA_MAX_REINTENTOS + 1} intentos: 503")]
    assert tareas.avisos == [(org, msg, "No pudimos abrir su audio")]


def test_tarea_sin_fallo_no_avisa(monkeypatch, tareas):
    monkeypatch.setattr(transcripcion, "transcribir_mensaje",
                        lambda *a: Resultado("transcrito"))
    org, msg = str(uuid.uuid4()), str(uuid.uuid4())
    assert tareas.transcribir_audio.apply(args=(org, msg)).get() == "transcrito"
    assert tareas.avisos == []
    # Ya transcrito, sigue la conversación (extracción y respuesta).
    assert tareas.conversaciones == [(org, msg)]


def test_tarea_usa_whisper_con_el_maximo_de_las_reglas(monkeypatch, tareas):
    llamadas = []
    monkeypatch.setattr(tareas.whisper, "transcribir",
                        lambda path, max_duracion_s: llamadas.append((path, max_duracion_s)))
    tareas._transcriptor(Path("a.ogg"), 180)
    assert llamadas == [(Path("a.ogg"), 180)]


def test_espera_entre_reintentos_crece_hasta_un_tope():
    from app.worker import tasks

    assert [tasks._espera_descarga(i) for i in range(5)] == [30, 60, 120, 240, 300]


def test_procesar_mensaje_encola_la_transcripcion_solo_para_audios(monkeypatch):
    from app.worker import tasks

    @contextmanager
    def uow():
        yield "repos"

    encolados, conversaciones = [], []
    monkeypatch.setattr(tasks, "unit_of_work", uow)
    monkeypatch.setattr(tasks, "get_channel", lambda: "canal")
    monkeypatch.setattr(tasks, "get_textos", lambda: "textos")
    monkeypatch.setattr(tasks.transcribir_audio, "delay", lambda *a: encolados.append(a))
    monkeypatch.setattr(tasks.atender_conversacion, "delay",
                        lambda *a: conversaciones.append(a))
    org, msg = str(uuid.uuid4()), str(uuid.uuid4())

    # Texto: directo a la conversación, sin transcribir.
    monkeypatch.setattr(tasks, "atender_mensaje", lambda *a: "listo_para_procesar")
    tasks.procesar_mensaje(org, msg)
    assert encolados == [] and conversaciones == [(org, msg)]

    monkeypatch.setattr(tasks, "atender_mensaje", lambda *a: "listo_para_transcribir")
    assert tasks.procesar_mensaje(org, msg) == "listo_para_transcribir"
    assert encolados == [(org, msg)] and conversaciones == [(org, msg)]

    # Sin autorización no sigue nada.
    monkeypatch.setattr(tasks, "atender_mensaje", lambda *a: "consentimiento_solicitado")
    tasks.procesar_mensaje(org, msg)
    assert len(encolados) == 1 and len(conversaciones) == 1


def test_avisar_productor_reintenta_fallas_del_canal():
    from app.worker.tasks import avisar_productor

    assert avisar_productor.name == "avisar_productor"
    assert ChannelTemporaryError in avisar_productor.autoretry_for


def test_precarga_del_modelo_no_tumba_el_worker(monkeypatch, caplog):
    from app.worker import tasks

    def sin_internet():
        raise OSError("sin conexión a huggingface")

    monkeypatch.setattr(tasks.whisper, "modelo_desde_settings", sin_internet)
    tasks.precargar_modelo()
    assert "No se pudo precargar" in caplog.text
