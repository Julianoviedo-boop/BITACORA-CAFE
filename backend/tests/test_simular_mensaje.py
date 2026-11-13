"""scripts/simular_mensaje.py arma payloads que el canal entiende como los de OpenBSP."""

import importlib.util
from pathlib import Path

import pytest

from app.channels.openbsp import parse_openbsp_payload

_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "scripts" / "simular_mensaje.py",
    Path("/scripts/simular_mensaje.py"),
]


@pytest.fixture(scope="module")
def sim():
    path = next((p for p in _CANDIDATES if p.is_file()), None)
    if path is None:
        pytest.skip("No se encontró scripts/simular_mensaje.py")
    spec = importlib.util.spec_from_file_location("simular_mensaje", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_texto(sim):
    p = sim.construir_payload(telefono="+573101234501", numero="1000", texto="Hoy abonamos",
                              external_id="wamid.x")
    msg = parse_openbsp_payload(p)
    assert (msg.external_id, msg.channel_address, msg.sender_phone, msg.kind, msg.text) == (
        "wamid.x", "1000", "+573101234501", "texto", "Hoy abonamos")


def test_audio(sim):
    msg = parse_openbsp_payload(sim.construir_payload(telefono="+573101234501", numero="1",
                                                      audio="nota.ogg"))
    assert (msg.kind, msg.media_ref, msg.media_mime_type) == ("audio", "fixture://nota.ogg",
                                                              "audio/ogg")


def test_imagen_con_pie(sim):
    msg = parse_openbsp_payload(sim.construir_payload(telefono="+573101234501", numero="1",
                                                      imagen="foto.jpg", texto="Así quedó"))
    assert (msg.kind, msg.text) == ("imagen", "Así quedó")


def test_propio_se_ignora(sim):
    p = sim.construir_payload(telefono="+573101234501", numero="1", texto="x", propio=True)
    assert p["data"]["sender_address"] is None
    assert parse_openbsp_payload(p) is None


def test_external_id_distinto_por_defecto(sim):
    a = sim.construir_payload(telefono="+573101234501", numero="1", texto="x")
    b = sim.construir_payload(telefono="+573101234501", numero="1", texto="x")
    assert a["data"]["external_id"] != b["data"]["external_id"]


def test_sin_secreto_no_envia(sim, monkeypatch, capsys):
    monkeypatch.delenv("OPENBSP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(sim, "_leer_env", lambda: {})
    assert sim.main(["--telefono", "+573101234501", "--texto", "hola"]) == 2
    assert "OPENBSP_WEBHOOK_SECRET" in capsys.readouterr().err
