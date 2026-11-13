"""Textos y respuestas de la autorización de datos (sin base de datos)."""

from pathlib import Path

import pytest

from app.domain.consentimiento import cargar_textos, clasificar_respuesta, normalizar

_CANDIDATES = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


@pytest.fixture(scope="module")
def textos():
    config_dir = next((p for p in _CANDIDATES if (p / "consentimiento.yaml").is_file()), None)
    if config_dir is None:
        pytest.skip("No se encontró config/consentimiento.yaml")
    return cargar_textos(config_dir)


@pytest.mark.parametrize(
    "texto,esperado",
    [("SÍ", "si"), ("si", "si"), ("Sí.", "si"), ("  sí 👍 ", "si"), ("Si señor", "si"),
     ("ACEPTO", "si"), ("no", "no"), ("No acepto!", "no"), ("NO.", "no"),
     ("sí pero mañana", None), ("Hoy abonamos", None), ("", None), (None, None)],
)
def test_clasificar_respuesta(textos, texto, esperado):
    assert clasificar_respuesta(texto, textos) == esperado


def test_normalizar():
    assert normalizar("¡Sí, Señora!") == "si senora"


def test_solicitud_lleva_el_nombre_de_la_organizacion(textos):
    texto = textos.solicitud_para("Asociación {rara}")
    assert "Asociación {rara}" in texto and "{organizacion}" not in texto
    assert "SÍ" in texto and "1581" in texto


def test_yaml_incompleto(tmp_path):
    (tmp_path / "consentimiento.yaml").write_text("solicitud: hola\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aceptado"):
        cargar_textos(tmp_path)


def test_yaml_sin_comillas_falla(tmp_path):
    (tmp_path / "consentimiento.yaml").write_text(
        "solicitud: a\naceptado: b\nrechazado: c\nrespuestas_si: [acepto]\nrespuestas_no: [no]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="comillas"):
        cargar_textos(tmp_path)


def test_yaml_con_respuesta_en_si_y_no(tmp_path):
    (tmp_path / "consentimiento.yaml").write_text(
        "solicitud: a\naceptado: b\nrechazado: c\nrespuestas_si: [sí]\nrespuestas_no: [SI]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="a la vez"):
        cargar_textos(tmp_path)
