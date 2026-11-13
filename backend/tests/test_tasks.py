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
