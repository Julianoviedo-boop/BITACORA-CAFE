"""Normalización de lo que devuelve Claude y caso de uso de extracción (sin red)."""

import datetime as dt
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.repositories import Repositories
from app.extraction.extractor import extraer, lotes_activos, normalizar
from app.extraction.llm import UsoTokens
from app.extraction.prompts import LoteContexto
from app.extraction.schema import cargar_catalogo, construir_modelos

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]
LOTES = [LoteContexto("MIR-01", "La Esperanza"), LoteContexto("MIR-02", "El Guamo")]
HOY = dt.date(2026, 11, 12)


@pytest.fixture(scope="module")
def catalogo():
    d = next((p for p in _CONFIG if (p / "eventos.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/eventos.yaml")
    return cargar_catalogo(d)


def _salida(catalogo, *eventos):
    base = {"fragmento": "texto", "lote_mencionado": None, "codigo_lote": None, "fecha": None,
            "hora": None, "valores": [], "faltantes": []}
    return construir_modelos(catalogo).Extraccion.model_validate(
        {"eventos": [base | e for e in eventos]})


def test_evento_completo(catalogo):
    s = _salida(catalogo, {"tipo_evento": "abonada", "codigo_lote": "mir-01",
                           "fecha": "2026-11-12", "valores": [
                               {"campo": "insumo", "valor": "17-6-18", "unidad": None},
                               {"campo": "cantidad", "valor": 4.0, "unidad": "bulto"}]})
    [e] = normalizar(s, catalogo, LOTES, HOY)
    assert e.codigo_lote == "MIR-01"  # se corrige a como está en la base
    assert e.datos["insumo"].valor == "17-6-18"
    assert (e.datos["cantidad"].valor, e.datos["cantidad"].unidad) == (4, "bulto")
    assert e.datos["dosis"] is None and e.datos["jornales"] is None  # no lo dijo: null
    assert e.faltantes == [] and e.advertencias == []


def test_faltantes_se_recalculan(catalogo):
    # El modelo dice que no falta nada y pide un opcional; se corrige con el catálogo.
    s = _salida(catalogo, {"tipo_evento": "fumigacion", "faltantes": ["dias_carencia"]})
    [e] = normalizar(s, catalogo, LOTES, HOY)
    assert e.faltantes == ["lote", "fecha", "producto"]


def test_venta_no_exige_lote(catalogo):
    s = _salida(catalogo, {"tipo_evento": "venta", "fecha": "2026-11-11",
                           "valores": [{"campo": "cantidad", "valor": 2, "unidad": "carga"}]})
    assert normalizar(s, catalogo, LOTES, HOY)[0].faltantes == []


def test_lote_de_otra_organizacion_o_inventado(catalogo):
    s = _salida(catalogo, {"tipo_evento": "desyerba", "codigo_lote": "VIC-02",
                           "fecha": "2026-11-12"})
    [e] = normalizar(s, catalogo, LOTES, HOY)
    assert e.codigo_lote is None and e.faltantes == ["lote"]
    assert "no es un lote activo" in e.advertencias[0]


def test_fecha_futura_se_quita(catalogo):
    s = _salida(catalogo, {"tipo_evento": "desyerba", "codigo_lote": "MIR-01",
                           "fecha": "2026-11-19"})
    [e] = normalizar(s, catalogo, LOTES, HOY)
    assert e.fecha is None and e.faltantes == ["fecha"]


def test_valores_raros(catalogo):
    s = _salida(catalogo, {"tipo_evento": "abonada", "valores": [
        {"campo": "cantidad", "valor": "1,5", "unidad": "litro"},     # número en texto, unidad mala
        {"campo": "cantidad", "valor": 9, "unidad": "kg"},            # repetido
        {"campo": "producto", "valor": "Verdadero", "unidad": None},  # campo de otro tipo
        {"campo": "jornales", "valor": "varios", "unidad": "jornal"},  # no es número
        {"campo": "insumo", "valor": "  ", "unidad": None},           # vacío
    ]})
    [e] = normalizar(s, catalogo, LOTES, HOY)
    assert (e.datos["cantidad"].valor, e.datos["cantidad"].unidad) == (1.5, None)
    assert e.datos["jornales"] is None and e.datos["insumo"] is None
    assert "producto" not in e.datos
    assert len(e.advertencias) == 4
    assert e.faltantes == ["lote", "fecha", "insumo"]


class ClienteFalso:
    def __init__(self, salida):
        self.salida = salida
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        uso = UsoTokens("claude-haiku-5-5", 10, 5, 0, 0, 1, 0.1, "req")
        return self.salida, uso


def test_extraer_usa_fecha_de_colombia_y_lotes_de_la_organizacion(catalogo):
    # 13 nov 2:00 UTC = 12 nov en Colombia: una fecha del 12 no es futura.
    s = _salida(catalogo, {"tipo_evento": "desyerba", "codigo_lote": "MIR-02",
                           "fecha": "2026-11-12"})
    cliente = ClienteFalso(s)
    r = extraer("hoy desyerbamos El Guamo", recibido_en=dt.datetime(2026, 11, 13, 2, tzinfo=dt.UTC),
                lotes=LOTES, cliente=cliente, catalogo=catalogo)
    assert r.eventos[0].fecha == dt.date(2026, 11, 12)
    assert r.uso["input_tokens"] == 10
    assert "MIR-02 | El Guamo" in cliente.kwargs["mensaje"]
    assert "hoy desyerbamos El Guamo" in cliente.kwargs["mensaje"]
    assert cliente.kwargs["output_format"].__name__ == "Extraccion"


@pytest.mark.db
def test_lotes_activos_solo_de_la_organizacion(db_session: Session):
    repos = Repositories(db_session)
    org, otra = uuid.uuid4(), uuid.uuid4()
    for o in (org, otra):
        repos.organizations.create(o, name="Org", slug=f"org-{o.hex[:8]}")
    finca = repos.farms.add(org, name="El Mirador")
    repos.lots.add(org, farm_id=finca.id, name="La Esperanza", code="ESP-01", variety="Castillo")
    repos.lots.add(org, farm_id=finca.id, name="Vieja", code="ESP-02", status="inactivo")
    finca_otra = repos.farms.add(otra, name="Ajena")
    repos.lots.add(otra, farm_id=finca_otra.id, name="Ajeno", code="AJ-01")

    assert lotes_activos(repos, org) == [
        LoteContexto("ESP-01", "La Esperanza", "Castillo", "El Mirador")]
