"""Piezas de la conversación que no necesitan base de datos: textos, qué quiere el
productor, formato del resumen, búsqueda de lotes, ventana de 24 horas y el
mensaje para Claude cuando el productor corrige."""

import datetime as dt
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.channels import VENTANA_ATENCION, dentro_de_ventana
from app.domain.conversacion import (
    buscar_lote,
    cargar_textos,
    clasificar,
    formatear_fecha,
    formatear_hora,
    formatear_numero,
    linea_evento,
    nombre_lote_nuevo,
    planear,
)
from app.extraction import prompts
from app.extraction.extractor import DatoMedido, EventoPropuesto, a_salida, revisar
from app.extraction.llm import UsoTokens
from app.extraction.prompts import LoteContexto
from app.extraction.schema import cargar_catalogo, construir_modelos

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]
HOY = dt.date(2026, 11, 13)  # viernes


def _config_dir() -> Path:
    d = next((p for p in _CONFIG if (p / "conversacion.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/conversacion.yaml")
    return d


@pytest.fixture(scope="module")
def textos():
    return cargar_textos(_config_dir())


@pytest.fixture(scope="module")
def catalogo():
    return cargar_catalogo(_config_dir())


# ---------------------------------------------------------------------------
# config/conversacion.yaml
# ---------------------------------------------------------------------------


def test_textos_del_repositorio(textos):
    assert textos.max_rondas == 3
    assert textos.t("confirmar_uno", evento="Lote 3: volteo, hoy 7:30 a.m., 38 °C") == \
        "Lote 3: volteo, hoy 7:30 a.m., 38 °C. ¿Está bien? Responde SÍ o dime qué cambiar."
    assert textos.prefijos_resumen[0] == "resumen del lote"  # el más largo primero
    assert textos.unidades["bulto"] == ("bulto", "bultos") and textos.unidades["kg"] == ("kg", "kg")


def test_todas_las_preguntas_obligatorias_estan_escritas(catalogo):
    """Cada campo obligatorio de eventos.yaml trae su pregunta para el productor."""
    sin_pregunta = [f"{t.clave}.{c.clave}" for t in catalogo.tipos.values()
                    for c in t.campos.values() if c.obligatorio and not c.pregunta]
    assert sin_pregunta == []


_MINIMO = """
max_rondas: 3
respuestas: {confirmar: ["sí"], negar: ["no"], cancelar: ["cancelar"]}
comandos: {mis_lotes: ["mis lotes"], ayuda: ["ayuda"], resumen: ["resumen"]}
preguntas: {lote: a, fecha: b, generica: c}
textos: {%s}
"""


def _yaml_minimo(**cambios: str) -> str:
    from app.domain.conversacion import CLAVES_TEXTOS

    base = _MINIMO % ", ".join(f"{k}: x" for k in CLAVES_TEXTOS)
    for viejo, nuevo in cambios.items():
        base = base.replace(viejo, nuevo)
    return base


def test_yaml_minimo_carga(tmp_path):
    (tmp_path / "conversacion.yaml").write_text(_yaml_minimo(), encoding="utf-8")
    assert cargar_textos(tmp_path).confirmar == frozenset({"si"})


@pytest.mark.parametrize("cambio,error", [
    ({"max_rondas: 3": "max_rondas: 0"}, "max_rondas"),
    ({'negar: ["no"]': "negar: [no]"}, "comillas"),
    ({'cancelar: ["cancelar"]': 'cancelar: ["SÍ"]'}, "está en"),
    ({'ayuda: ["ayuda"]': 'ayuda: ["mis lotes"]'}, "está en"),
    ({"generica: c": "generica: ''"}, "generica"),
    ({"confirmado: x, ": ""}, "confirmado"),
])
def test_yaml_invalido(tmp_path, cambio, error):
    (tmp_path / "conversacion.yaml").write_text(_yaml_minimo(**cambio), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        cargar_textos(tmp_path)


# ---------------------------------------------------------------------------
# Qué quiere el productor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("texto,esperado", [
    ("Sí", ("confirmar", None)), ("SI 👍", ("confirmar", None)), ("Sí, está bien.", ("confirmar", None)),
    ("listo", ("confirmar", None)), ("No", ("negar", None)), ("CANCELAR", ("cancelar", None)),
    ("Bórralo", ("cancelar", None)), ("Mis lotes", ("mis_lotes", None)), ("ayuda", ("ayuda", None)),
    ("Resumen lote 3", ("resumen", "3")), ("resumen del lote La Esperanza", ("resumen", "la esperanza")),
    ("resumen", ("resumen", None)),
    ("Resumen de la semana: el lunes abonamos el 3 y el martes fumigamos", ("texto", None)),
    ("sí, pero fueron 5 bultos", ("texto", None)), ("👍", ("vacio", None)), ("", ("vacio", None)),
    (None, ("vacio", None)),
])
def test_clasificar(textos, texto, esperado):
    assert clasificar(texto, textos) == esperado


@pytest.mark.parametrize("estado,rondas,intencion,accion", [
    ("libre", 0, "texto", "extraer"),
    ("libre", 0, "confirmar", "nada_pendiente"),
    ("libre", 0, "cancelar", "nada_pendiente"),
    ("libre", 0, "mis_lotes", "mis_lotes"),
    ("confirmando", 0, "confirmar", "confirmar"),
    ("confirmando", 0, "negar", "que_cambiar"),
    ("confirmando", 0, "texto", "revisar"),
    ("confirmando", 2, "texto", "revisar"),
    ("confirmando", 3, "texto", "asesor"),
    ("confirmando", 3, "confirmar", "confirmar"),
    ("confirmando", 0, "cancelar", "cancelar"),
    ("confirmando", 0, "resumen", "resumen"),
    ("preguntando", 0, "texto", "revisar"),
    ("preguntando", 0, "confirmar", "revisar"),  # "sí" no es respuesta a "¿qué abono?"
    ("preguntando", 0, "cancelar", "cancelar"),
    ("ofreciendo_lote", 0, "confirmar", "crear_lote"),
    ("ofreciendo_lote", 0, "negar", "rechazar_lote"),
    ("ofreciendo_lote", 0, "texto", "revisar"),
])
def test_planear(textos, estado, rondas, intencion, accion):
    assert planear(estado, rondas, intencion, textos) == accion


def test_solo_el_si_en_confirmando_confirma(textos):
    """Regla de oro 2: ninguna otra combinación de estado e intención confirma."""
    intenciones = ["vacio", "mis_lotes", "ayuda", "resumen", "confirmar", "negar", "cancelar",
                   "texto"]
    confirman = [(e, i) for e in ("libre", "preguntando", "ofreciendo_lote", "confirmando")
                 for i in intenciones for r in (0, 5)
                 if planear(e, r, i, textos) == "confirmar"]
    assert set(confirman) == {("confirmando", "confirmar")}


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valor,texto", [
    (4, "4"), (4.0, "4"), (2.5, "2,5"), (0.25, "0,25"), (2150000, "2.150.000"),
    (1234.5, "1.234,5"), (-3, "-3"),
])
def test_formatear_numero(valor, texto):
    assert formatear_numero(valor) == texto


@pytest.mark.parametrize("hora,texto", [
    (dt.time(7, 30), "7:30 a.m."), (dt.time(0, 5), "12:05 a.m."), (dt.time(12, 0), "12:00 p.m."),
    (dt.time(15, 45), "3:45 p.m."),
])
def test_formatear_hora(hora, texto):
    assert formatear_hora(hora) == texto


@pytest.mark.parametrize("fecha,texto", [
    (HOY, "hoy"), (dt.date(2026, 11, 12), "ayer"), (dt.date(2026, 11, 11), "antier"),
    (dt.date(2026, 11, 9), "lunes 9 de noviembre"),
    (dt.date(2025, 12, 31), "miércoles 31 de diciembre de 2025"),
])
def test_formatear_fecha(fecha, texto):
    assert formatear_fecha(fecha, HOY) == texto


def test_linea_evento(textos, catalogo):
    datos = {"insumo": DatoMedido(valor="17-6-18"), "cantidad": DatoMedido(valor=1, unidad="bulto"),
             "dosis": None, "jornales": DatoMedido(valor=3, unidad="jornal")}
    assert linea_evento("abonada", "Lote 3", HOY, dt.time(7, 30), datos, HOY, textos, catalogo) == \
        "Lote 3: abonada, hoy 7:30 a.m., 17-6-18, 1 bulto, 3 jornales"


def test_linea_evento_tolera_tipos_y_campos_viejos(textos, catalogo):
    datos = {"temperatura": DatoMedido(valor=38, unidad="grados")}
    assert linea_evento("volteo", "Lote 3", HOY, None, datos, HOY, textos, catalogo) == \
        "Lote 3: volteo, hoy, 38 grados"


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------


def _lote(code: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), code=code, name=name)


LOTES = [_lote("LOTE-001", "La Esperanza"), _lote("LOTE-003", "Lote 3"),
         _lote("MIR-02", "El Guamo")]


@pytest.mark.parametrize("ref,codigo", [
    ("3", "LOTE-003"), ("lote 3", "LOTE-003"), ("el 3", "LOTE-003"), ("Lote 3", "LOTE-003"),
    ("la esperanza", "LOTE-001"), ("Esperanza", "LOTE-001"), ("lote-001", "LOTE-001"),
    ("1", "LOTE-001"), ("el guamo", "MIR-02"), ("2", "MIR-02"), ("9", None), ("", None),
    ("el de arriba", None),
])
def test_buscar_lote(ref, codigo):
    lote = buscar_lote(ref, LOTES)
    assert (lote.code if lote else None) == codigo


def test_buscar_lote_ambiguo():
    lotes = [_lote("A-3", "Arriba"), _lote("B-3", "Abajo")]
    assert buscar_lote("3", lotes) is None


@pytest.mark.parametrize("mencion,nombre", [
    ("el 7", "Lote 7"), ("lote 7", "Lote 7"), ("el lote 07", "Lote 7"), ("7", "Lote 7"),
    ("el lote número 7", "Lote 7"), ("el de arriba", "El de arriba"),
    ("el lote Los Pinos", "Los Pinos"), ("La Cañada", "La Cañada"),
])
def test_nombre_lote_nuevo(mencion, nombre):
    assert nombre_lote_nuevo(mencion) == nombre


# ---------------------------------------------------------------------------
# Ventana de 24 horas (app/channels/ventana.py)
# ---------------------------------------------------------------------------

AHORA = dt.datetime(2026, 11, 13, 15, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("hace,dentro", [
    (dt.timedelta(minutes=1), True), (dt.timedelta(hours=23, minutes=59), True),
    (VENTANA_ATENCION, False), (dt.timedelta(days=3), False),
])
def test_dentro_de_ventana(hace, dentro):
    assert dentro_de_ventana(AHORA - hace, AHORA) is dentro


def test_sin_mensajes_del_usuario_no_hay_ventana():
    assert dentro_de_ventana(None, AHORA) is False


def test_ventana_exige_zona_horaria():
    with pytest.raises(ValueError):
        dentro_de_ventana(dt.datetime(2026, 11, 13), AHORA)


# ---------------------------------------------------------------------------
# Corrección: mensaje para Claude y normalización
# ---------------------------------------------------------------------------

LOTES_CTX = [LoteContexto("LOTE-003", "Lote 3")]
RECIBIDO = dt.datetime(2026, 11, 13, 15, 0, tzinfo=dt.UTC)


def _propuesto(**cambios) -> EventoPropuesto:
    base = dict(tipo_evento="abonada", codigo_lote="LOTE-003", lote_mencionado="el 3",
                fecha=HOY, hora=dt.time(7, 30),
                datos={"insumo": DatoMedido(valor="17-6-18"),
                       "cantidad": DatoMedido(valor=4, unidad="bulto"), "dosis": None,
                       "jornales": None},
                faltantes=[], fragmento="abonamos el 3 <script>")
    return EventoPropuesto(**(base | cambios))


def test_a_salida_vuelve_al_formato_de_claude(catalogo):
    salida = a_salida(_propuesto())
    assert salida["valores"] == [{"campo": "insumo", "valor": "17-6-18", "unidad": None},
                                 {"campo": "cantidad", "valor": 4, "unidad": "bulto"}]
    assert (salida["fecha"], salida["hora"]) == ("2026-11-13", "07:30:00")
    # Lo que devuelve a_salida lo acepta el esquema de la salida estructurada.
    construir_modelos(catalogo).Extraccion.model_validate({"eventos": [salida]})


def test_mensaje_de_revision():
    m = prompts.construir_mensaje_revision([a_salida(_propuesto())], "No, fueron <5> bultos",
                                           RECIBIDO, LOTES_CTX, "¿Está bien?")
    assert m.index("<contexto>") < m.index("<eventos_propuestos>") < \
        m.index("<pregunta_al_productor>") < m.index("<mensaje_productor>") < m.index("<tarea>")
    assert "No, fueron ‹5› bultos" in m  # el texto del productor no abre etiquetas
    assert "<script>" not in m and "‹script›" in m
    assert "- LOTE-003 | Lote 3" in m


def test_mensaje_de_revision_sin_pregunta():
    m = prompts.construir_mensaje_revision([], "hola", RECIBIDO, LOTES_CTX)
    assert "<pregunta_al_productor>" not in m


class ClienteFalso:
    def __init__(self, salida):
        self.salida, self.kwargs = salida, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.salida, UsoTokens("claude-haiku-5-5", 10, 5, 0, 0, 1, 0.1, "req")


def test_revisar_normaliza_y_usa_el_mismo_system(catalogo):
    salida = construir_modelos(catalogo).Extraccion.model_validate({"eventos": [
        a_salida(_propuesto()) | {"valores": [
            {"campo": "insumo", "valor": "17-6-18", "unidad": None},
            {"campo": "cantidad", "valor": "5", "unidad": "bulto"}]}]})
    cliente = ClienteFalso(salida)
    r = revisar([_propuesto()], "fueron 5", recibido_en=RECIBIDO, lotes=LOTES_CTX,
                pregunta="¿Está bien?", cliente=cliente, catalogo=catalogo)

    [e] = r.eventos
    assert e.datos["cantidad"] == DatoMedido(valor=5, unidad="bulto")  # "5" -> 5
    assert cliente.kwargs["system"] == prompts.construir_system(catalogo)  # misma caché
    assert cliente.kwargs["etiqueta"] == "revision"
    assert "<eventos_propuestos>" in cliente.kwargs["mensaje"]
