"""config/alertas.yaml: lectura, validación y mensajes de error claros."""

import textwrap
from pathlib import Path

import pytest

from app.domain.alertas import AlertasInvalidasError, cargar_reglas
from app.extraction.schema import cargar_catalogo

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


def _config_dir() -> Path:
    d = next((p for p in _CONFIG if (p / "alertas.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/alertas.yaml")
    return d


@pytest.fixture(scope="module")
def catalogo():
    return cargar_catalogo(_config_dir())


BASE = """
etapas:
  fermentacion:
    empieza_con:
      - evento: beneficio
        campo: etapa
        dice: ferment
alertas_por_valor:
  - etapa: fermentacion
    campo: temperatura
    minimo: 18
    maximo: 40
    mensaje: "Lote {lote}: {valor} grados"
alertas_por_tiempo:
  - etapa: fermentacion
    evento_esperado: volteo
    horas_limite: 12
    mensaje: "Lote {lote}: {horas} horas sin volteo"
"""


def _cargar(tmp_path: Path, catalogo, contenido: str):
    (tmp_path / "alertas.yaml").write_text(textwrap.dedent(contenido), encoding="utf-8")
    return cargar_reglas(tmp_path, catalogo)


def _error(tmp_path: Path, catalogo, contenido: str) -> str:
    with pytest.raises(AlertasInvalidasError) as exc:
        _cargar(tmp_path, catalogo, contenido)
    return str(exc.value)


# --- el archivo real --------------------------------------------------------


def test_el_archivo_real_es_valido(catalogo) -> None:
    reglas = cargar_reglas(_config_dir(), catalogo)
    assert set(reglas.etapas) == {"fermentacion", "secado"}
    assert [r.codigo for r in reglas.alertas_por_valor] == [
        "valor:fermentacion:temperatura", "valor:secado:humedad"]
    assert [r.codigo for r in reglas.alertas_por_tiempo] == [
        "tiempo:fermentacion:volteo", "tiempo:secado:medicion_humedad"]
    # Todavía no hay plantilla aprobada en Meta.
    assert reglas.plantilla.nombre == ""


def test_base_valida_y_valores_por_defecto(tmp_path, catalogo) -> None:
    reglas = _cargar(tmp_path, catalogo, BASE)
    r = reglas.alertas_por_valor[0]
    assert (r.minimo, r.maximo, r.unidad) == (18.0, 40.0, None)
    assert reglas.alertas_por_tiempo[0].horas_limite == 12
    assert reglas.plantilla.idioma == "es"


def test_archivo_vacio_no_tiene_reglas(tmp_path, catalogo) -> None:
    reglas = _cargar(tmp_path, catalogo, "# nada todavía\n")
    assert reglas.alertas_por_valor == [] and reglas.alertas_por_tiempo == []


def test_solo_minimo_o_solo_maximo(tmp_path, catalogo) -> None:
    reglas = _cargar(tmp_path, catalogo, BASE.replace("    maximo: 40\n", ""))
    assert reglas.alertas_por_valor[0].maximo is None


# --- YAML inválido ----------------------------------------------------------


def test_falta_un_dato(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("    campo: temperatura\n", ""))
    assert "alertas_por_valor → regla 1 → campo: falta este dato" in msg


def test_dato_mal_escrito(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("maximo: 40", "maximmo: 40"))
    assert "regla 1 → maximmo: no se reconoce este dato (¿está bien escrito?)" in msg


def test_numero_con_coma(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("maximo: 40", 'maximo: "12,5"'))
    assert "regla 1 → maximo: debe ser un número; los decimales van con punto" in msg


def test_minimo_mayor_que_maximo(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("minimo: 18", "minimo: 45"))
    assert "minimo (45) debe ser menor que maximo (40)" in msg


def test_sin_rango(tmp_path, catalogo) -> None:
    sin_rango = BASE.replace("    minimo: 18\n", "").replace("    maximo: 40\n", "")
    assert "necesita minimo, maximo o los dos" in _error(tmp_path, catalogo, sin_rango)


def test_horas_limite_cero(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("horas_limite: 12", "horas_limite: 0"))
    assert "alertas_por_tiempo → regla 1 → horas_limite: debe ser mayor que 0" in msg


def test_campo_que_no_existe(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("campo: temperatura", "campo: temperatur"))
    assert "'temperatur' no existe en ningún evento de config/eventos.yaml" in msg


def test_campo_de_texto(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("campo: temperatura", "campo: insumo"))
    assert "'insumo' es de texto; solo se revisan números" in msg


def test_campo_con_varias_unidades_pide_unidad(tmp_path, catalogo) -> None:
    # "cantidad" se mide en bultos, kilos, arrobas... no se sabe con qué comparar.
    msg = _error(tmp_path, catalogo, BASE.replace("campo: temperatura", "campo: cantidad"))
    assert "unidad: 'cantidad' se puede medir en" in msg
    assert "diga cuál con unidad:" in msg
    ok = BASE.replace("campo: temperatura", "campo: cantidad\n    unidad: kg")
    assert _cargar(tmp_path, catalogo, ok).alertas_por_valor[0].unidad == "kg"


def test_evento_esperado_que_no_existe(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("evento_esperado: volteo", "evento_esperado: volteos"))
    assert "evento_esperado: 'volteos' no existe en config/eventos.yaml" in msg


def test_etapa_no_declarada(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("  - etapa: fermentacion\n    evento_esperado",
                                                   "  - etapa: secado\n    evento_esperado"))
    assert "etapa: 'secado' no está en etapas (hay: fermentacion)" in msg


def test_condicion_de_etapa_con_evento_inexistente(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("evento: beneficio", "evento: benefisio"))
    assert "etapas → fermentacion → empieza_con → condición 1 → evento: 'benefisio' no existe" in msg


def test_condicion_dice_en_campo_numerico(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("campo: etapa", "campo: cantidad"))
    assert "'cantidad' es un número; dice solo sirve con campos de texto" in msg


def test_condicion_campo_sin_dice(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("        dice: ferment\n", ""))
    assert "campo y dice van juntos" in msg


def test_etapa_sin_condiciones(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, """
        etapas:
          fermentacion:
            empieza_con: []
        """)
    assert "etapas → fermentacion → empieza_con: no puede quedar vacío" in msg


def test_llave_que_no_se_puede_usar(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("{horas} horas", "{valor} horas"))
    assert "mensaje: {valor} no se puede usar aquí; se puede usar {horas}, {lote}" in msg


def test_llave_sin_cerrar(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, BASE.replace("Lote {lote}: {valor}", "Lote {lote: {valor}"))
    assert "regla 1 (fermentacion / temperatura) → mensaje:" in msg


def test_regla_repetida(tmp_path, catalogo) -> None:
    repetida = BASE.replace("alertas_por_tiempo:", """  - etapa: fermentacion
    campo: temperatura
    maximo: 35
    mensaje: "otra"
alertas_por_tiempo:""")
    assert "regla 2 (fermentacion / temperatura): está repetida" in _error(tmp_path, catalogo, repetida)


def test_yaml_danado(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, "etapas:\n  fermentacion:\n empieza_con: x\n   - y\n")
    assert "el formato está dañado (línea" in msg


def test_lista_escrita_como_texto(tmp_path, catalogo) -> None:
    msg = _error(tmp_path, catalogo, "alertas_por_valor: temperatura\n")
    assert "alertas_por_valor: debe ser una lista" in msg


def test_archivo_que_no_existe(tmp_path, catalogo) -> None:
    with pytest.raises(AlertasInvalidasError, match="No se encontró"):
        cargar_reglas(tmp_path, catalogo)


def test_lista_todos_los_errores_de_una_vez(tmp_path, catalogo) -> None:
    malo = (BASE.replace("campo: temperatura", "campo: temperatur")
                .replace("evento_esperado: volteo", "evento_esperado: volteos"))
    msg = _error(tmp_path, catalogo, malo)
    assert "'temperatur'" in msg and "'volteos'" in msg
    assert msg.count("\n  - ") == 2
