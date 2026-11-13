"""Catálogo (config/eventos.yaml) y modelos Pydantic generados para la extracción."""

import json
from pathlib import Path

import pytest
from anthropic.lib._parse._transform import transform_schema
from pydantic import TypeAdapter, ValidationError

from app.extraction.schema import (
    CatalogoInvalidoError,
    cargar_catalogo,
    construir_modelos,
)

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


@pytest.fixture(scope="module")
def catalogo():
    d = next((p for p in _CONFIG if (p / "eventos.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/eventos.yaml")
    return cargar_catalogo(d)


def _yaml(tmp_path: Path, texto: str) -> Path:
    (tmp_path / "eventos.yaml").write_text(texto, encoding="utf-8")
    return tmp_path


MINIMO = """
unidades: {kg: kilos}
tipos:
  abonada:
    nombre: Abonada
    descripcion: Abono.
    campos:
      insumo: {tipo: texto, obligatorio: true}
      cantidad: {tipo: numero, unidades: [kg]}
  venta:
    nombre: Venta
    descripcion: Venta.
    requiere_lote: false
"""


def test_catalogo_del_repositorio(catalogo):
    assert {"abonada", "fumigacion", "recoleccion", "venta"} <= set(catalogo.tipos)
    assert catalogo.tipos["abonada"].obligatorios() == ["lote", "fecha", "insumo", "cantidad"]
    assert catalogo.tipos["venta"].obligatorios() == ["fecha", "cantidad"]
    for t in catalogo.tipos.values():
        for c in t.campos.values():
            assert set(c.unidades) <= set(catalogo.unidades)


def test_catalogo_minimo(tmp_path):
    cat = cargar_catalogo(_yaml(tmp_path, MINIMO))
    assert cat.campos == {"insumo": "texto", "cantidad": "numero"}
    assert cat.faltantes_posibles() == ["lote", "fecha", "insumo", "cantidad"]


@pytest.mark.parametrize("original, cambio, mensaje", [
    ("{tipo: texto, obligatorio: true}", "{tipo: texto, unidades: [kg]}",
     "texto no lleva unidades"),
    ("{tipo: numero, unidades: [kg]}", "{tipo: numero, unidades: [libra]}",
     "unidades desconocidas"),
    ("{tipo: numero, unidades: [kg]}", "{tipo: numero}", "necesita unidades"),
    ("{tipo: texto, obligatorio: true}", "{tipo: fecha}", "numero o texto"),
])
def test_catalogo_invalido(tmp_path, original, cambio, mensaje):
    malo = MINIMO.replace(original, cambio)
    assert malo != MINIMO
    with pytest.raises(CatalogoInvalidoError, match=mensaje):
        cargar_catalogo(_yaml(tmp_path, malo))


def test_campo_con_tipos_distintos_en_dos_eventos(tmp_path):
    malo = MINIMO + "    campos:\n      cantidad: {tipo: texto}\n"
    with pytest.raises(CatalogoInvalidoError, match="ya existe"):
        cargar_catalogo(_yaml(tmp_path, malo))


def test_nombres_reservados(tmp_path):
    malo = MINIMO.replace("insumo:", "fecha:")
    with pytest.raises(CatalogoInvalidoError, match="reservado"):
        cargar_catalogo(_yaml(tmp_path, malo))


def test_yaml_vacio(tmp_path):
    with pytest.raises(CatalogoInvalidoError):
        cargar_catalogo(_yaml(tmp_path, ""))


def test_modelos_restringen_a_lo_del_catalogo(tmp_path):
    m = construir_modelos(cargar_catalogo(_yaml(tmp_path, MINIMO)))
    ok = {"eventos": [{"tipo_evento": "abonada", "fragmento": "abonamos", "lote_mencionado": None,
                       "codigo_lote": None, "fecha": "2026-11-11", "hora": None,
                       "valores": [{"campo": "cantidad", "valor": 4, "unidad": "kg"}],
                       "faltantes": ["lote", "insumo"]}]}
    assert m.Extraccion.model_validate(ok).eventos[0].fecha.isoformat() == "2026-11-11"
    for ruta, malo in [("tipo_evento", "poda"), ("faltantes", ["precio"])]:
        roto = json.loads(json.dumps(ok))
        roto["eventos"][0][ruta] = malo
        with pytest.raises(ValidationError):
            m.Extraccion.model_validate(roto)
    roto = json.loads(json.dumps(ok))
    roto["eventos"][0]["valores"][0]["unidad"] = "libra"
    with pytest.raises(ValidationError):
        m.Extraccion.model_validate(roto)


def test_esquema_dentro_de_los_limites_de_la_api(catalogo):
    """La API acepta máximo 24 opcionales y 16 parámetros con unión en todo el esquema."""
    esquema = transform_schema(TypeAdapter(construir_modelos(catalogo).Extraccion).json_schema())
    texto = json.dumps(esquema)
    assert texto.count('"anyOf"') <= 16

    def opcionales(o) -> int:
        if isinstance(o, dict):
            propio = len(set(o.get("properties", {})) - set(o.get("required", [])))
            return propio + sum(opcionales(v) for v in o.values())
        if isinstance(o, list):
            return sum(opcionales(v) for v in o)
        return 0

    assert opcionales(esquema) == 0
