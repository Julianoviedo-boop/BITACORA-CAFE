from app.worker.tasks import ping


def test_ping_directo():
    assert ping() == "pong"


def test_ping_como_tarea_local():
    # apply() ejecuta la tarea en el mismo proceso, sin broker.
    assert ping.apply().get() == "pong"


def test_ping_registrada_con_nombre():
    assert ping.name == "ping"


def test_procesar_mensaje_registrada_con_reintentos():
    from app.channels import ChannelTemporaryError
    from app.worker.tasks import procesar_mensaje

    assert procesar_mensaje.name == "procesar_mensaje"
    assert ChannelTemporaryError in procesar_mensaje.autoretry_for


def test_procesar_mensaje_llama_al_dominio_con_uuids(monkeypatch):
    import uuid
    from contextlib import contextmanager

    from app.worker import tasks

    llamadas = []

    @contextmanager
    def uow():
        yield "repos"

    monkeypatch.setattr(tasks, "unit_of_work", uow)
    monkeypatch.setattr(tasks, "get_channel", lambda: "canal")
    monkeypatch.setattr(tasks, "get_textos", lambda: "textos")
    monkeypatch.setattr(tasks, "atender_mensaje",
                        lambda *a: llamadas.append(a) or "consentimiento_solicitado")
    org, msg = uuid.uuid4(), uuid.uuid4()

    assert tasks.procesar_mensaje(str(org), str(msg)) == "consentimiento_solicitado"
    assert llamadas == [("repos", "canal", org, msg, "textos")]


# ---------------------------------------------------------------------------
# atender_conversacion
# ---------------------------------------------------------------------------


def _tarea_conversacion(monkeypatch, atender):
    from app.domain import conversacion
    from app.worker import tasks

    fallos = []
    monkeypatch.setattr(tasks, "get_channel", lambda: "canal")
    monkeypatch.setattr(tasks, "_interprete", lambda: "interprete")
    monkeypatch.setattr(conversacion, "get_textos", lambda: "textos")
    monkeypatch.setattr(conversacion, "atender", atender)
    monkeypatch.setattr(conversacion, "fallar",
                        lambda uow, canal, textos, org, msg, detalle: fallos.append(detalle)
                        or "error")
    return tasks, fallos


def test_atender_conversacion_registrada():
    from app.worker.tasks import CONVERSACION_MAX_REINTENTOS, atender_conversacion

    assert atender_conversacion.name == "atender_conversacion"
    assert atender_conversacion.max_retries == CONVERSACION_MAX_REINTENTOS


def test_atender_conversacion_llama_al_dominio(monkeypatch):
    import uuid

    llamadas = []
    tasks, _ = _tarea_conversacion(monkeypatch, lambda *a: llamadas.append(a) or "resumen")
    org, msg = uuid.uuid4(), uuid.uuid4()
    assert tasks.atender_conversacion.apply(args=(str(org), str(msg))).get() == "resumen"
    [(uow, canal, interprete, textos, org_id, msg_id)] = llamadas
    assert (canal, interprete, textos, org_id, msg_id) == ("canal", "interprete", "textos", org, msg)
    assert uow is tasks.unit_of_work


def test_atender_conversacion_reintenta_y_al_final_avisa(monkeypatch):
    import uuid

    from app.domain.conversacion import ReintentarError

    intentos = []

    def caido(*a):
        intentos.append(a)
        raise ReintentarError("Claude no responde")

    tasks, fallos = _tarea_conversacion(monkeypatch, caido)
    r = tasks.atender_conversacion.apply(args=(str(uuid.uuid4()), str(uuid.uuid4()))).get()
    assert r == "error"
    assert len(intentos) == tasks.CONVERSACION_MAX_REINTENTOS + 1
    assert fallos == [f"Falla temporal tras {tasks.CONVERSACION_MAX_REINTENTOS + 1} intentos: "
                      "Claude no responde"]


def test_atender_conversacion_con_el_canal_caido_no_avisa(monkeypatch):
    import uuid

    import pytest

    from app.channels import ChannelTemporaryError

    def caido(*a):
        raise ChannelTemporaryError("503")

    tasks, fallos = _tarea_conversacion(monkeypatch, caido)
    with pytest.raises(ChannelTemporaryError):
        tasks.atender_conversacion.apply(args=(str(uuid.uuid4()), str(uuid.uuid4()))).get()
    assert fallos == []
