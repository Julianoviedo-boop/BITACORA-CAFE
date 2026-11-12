from app.worker.tasks import ping


def test_ping_directo():
    assert ping() == "pong"


def test_ping_como_tarea_local():
    # apply() ejecuta la tarea en el mismo proceso, sin broker.
    assert ping.apply().get() == "pong"


def test_ping_registrada_con_nombre():
    assert ping.name == "ping"
