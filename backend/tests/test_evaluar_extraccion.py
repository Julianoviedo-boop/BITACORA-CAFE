"""Calificación de scripts/evaluar_extraccion.py (sin llamar a la API)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "scripts" / "evaluar_extraccion.py",
    Path("/scripts/evaluar_extraccion.py"),
]
CASOS = Path(__file__).parent / "fixtures" / "casos.jsonl"


@pytest.fixture(scope="module")
def ev():
    path = next((p for p in _CANDIDATES if p.is_file()), None)
    if path is None:
        pytest.skip("No se encontró scripts/evaluar_extraccion.py")
    spec = importlib.util.spec_from_file_location("evaluar_extraccion", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass lo necesita registrado
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


ESPERADO = [{"tipo_evento": "abonada", "codigo_lote": "MIR-01", "fecha": "2026-11-12",
             "hora": None, "faltantes": [],
             "datos": {"insumo": {"valor": "17-6-18"},
                       "cantidad": {"valor": 4, "unidad": "bulto"},
                       "jornales": {"valor": 3, "unidad": ["persona", "jornal"]}}}]


def _obtenido(**cambios):
    base = {"tipo_evento": "abonada", "codigo_lote": "MIR-01", "fecha": "2026-11-12",
            "hora": None, "faltantes": [], "fragmento": "...",
            "datos": {"insumo": {"valor": "17-6-18 ", "unidad": None},
                      "cantidad": {"valor": 4.0, "unidad": "bulto"},
                      "jornales": {"valor": 3, "unidad": "jornal"}, "dosis": None}}
    return [base | cambios]


def test_todo_bien(ev):
    m = ev.Marcador()
    ev.calificar_caso("c", ESPERADO, _obtenido(), m)
    assert m.errores == [] and m.inventados == 0
    assert all(m.aciertos[k] == m.totales[k] for k in m.totales)


def test_errores_e_inventados(ev):
    m = ev.Marcador()
    datos = {"insumo": {"valor": "urea", "unidad": None},
             "cantidad": {"valor": 4, "unidad": "kg"},
             "jornales": None, "dosis": {"valor": 10, "unidad": "g_por_planta"}}
    ev.calificar_caso("c", ESPERADO, _obtenido(datos=datos, fecha="2026-11-11"), m)
    assert m.aciertos["fecha"] == 0 and m.aciertos["valores.cantidad"] == 0
    assert m.aciertos["valores.insumo"] == 0 and m.aciertos["valores.jornales"] == 0
    assert m.inventados == 1 and any("INVENTÓ dosis" in e for e in m.errores)


def test_eventos_perdidos_y_sobrantes(ev):
    m = ev.Marcador()
    sobrante = _obtenido(tipo_evento="venta")
    ev.calificar_caso("c", ESPERADO, sobrante, m)
    assert (m.eventos_encontrados, m.eventos_sobrantes) == (0, 1)
    assert m.aciertos["caso_completo_eventos"] == 0
    m2 = ev.Marcador()
    ev.calificar_caso("vacio", [], [], m2)
    assert m2.aciertos["caso_completo_eventos"] == 1


def test_emparejar_elige_el_mas_parecido(ev):
    esperados = [dict(ESPERADO[0], codigo_lote="MIR-02")]
    obtenidos = _obtenido() + _obtenido(codigo_lote="MIR-02")
    pares, perdidos, sobrantes = ev.emparejar(esperados, obtenidos)
    assert pares[0][1]["codigo_lote"] == "MIR-02" and len(sobrantes) == 1 and not perdidos


def test_textos(ev):
    assert ev.texto_coincide("despulpado fermentación", "Despulpado y fermentacion")
    assert ev.texto_coincide("guadaña", "con guadaña")
    assert not ev.texto_coincide("pergamino seco", "pergamino")
    assert not ev.texto_coincide("roya", None)


def test_casos_del_repositorio_bien_formados(ev):
    casos = ev.cargar_casos(CASOS)
    assert len(casos) >= 20 and len({c["id"] for c in casos}) == len(casos)
    for c in casos:
        assert c["texto"] and c["lotes"] is not None
        for e in c["esperado"]:
            assert set(e) == {"tipo_evento", "codigo_lote", "fecha", "hora", "datos", "faltantes"}
            json.dumps(e)
