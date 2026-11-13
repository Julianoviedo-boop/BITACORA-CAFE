"""Motor de alertas: etapas, alertas por valor y alertas por tiempo.

Usa reglas propias (no las provisionales de config/alertas.yaml) para que las
pruebas no cambien cuando Brayan ajuste los valores.
"""

import datetime as dt
import textwrap
import uuid
from pathlib import Path

import pytest

from app.db.models import LotEvent
from app.db.repositories import Repositories
from app.domain import alertas
from app.extraction.prompts import ZONA_COLOMBIA
from app.extraction.schema import cargar_catalogo

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]

REGLAS = """
etapas:
  fermentacion:
    empieza_con:
      - evento: beneficio
        campo: etapa
        dice: ferment
  secado:
    empieza_con:
      - evento: beneficio
        campo: etapa
        dice: lav
    termina_con:
      - evento: secado
alertas_por_valor:
  - etapa: fermentacion
    campo: temperatura
    minimo: 18
    maximo: 40
    mensaje: "Fermentación del lote {lote} a {valor}°C (rango {minimo} a {maximo})."
  - etapa: secado
    campo: humedad
    minimo: 10
    maximo: 12.5
    mensaje: "Humedad del lote {lote} en {valor}%."
alertas_por_tiempo:
  - etapa: fermentacion
    evento_esperado: volteo
    horas_limite: 12
    mensaje: "El lote {lote} lleva más de {horas} horas sin volteo."
  - etapa: secado
    evento_esperado: medicion_humedad
    horas_limite: 48
    mensaje: "El lote {lote} lleva más de {horas} horas sin medir humedad."
"""

T0 = dt.datetime(2026, 11, 10, 8, 0, tzinfo=ZONA_COLOMBIA)  # martes 8:00 a.m.


def _h(horas: float) -> dt.datetime:
    return T0 + dt.timedelta(hours=horas)


@pytest.fixture(scope="module")
def reglas(tmp_path_factory) -> alertas.ReglasAlertas:
    config = next((p for p in _CONFIG if (p / "eventos.yaml").is_file()), None)
    if config is None:
        pytest.skip("No se encontró config/eventos.yaml")
    carpeta = tmp_path_factory.mktemp("alertas")
    (carpeta / "alertas.yaml").write_text(textwrap.dedent(REGLAS), encoding="utf-8")
    return alertas.cargar_reglas(carpeta, cargar_catalogo(config))


def ev(tipo: str, momento: dt.datetime, *, status: str = "confirmado",
       confirmado: dt.datetime | None = None, **datos) -> LotEvent:
    """Evento en memoria con la forma de lot_events.data: {campo: {valor, unidad}}."""
    data = {c: ({"valor": v[0], "unidad": v[1]} if isinstance(v, tuple) else {"valor": v, "unidad": None})
            for c, v in datos.items()}
    return LotEvent(id=uuid.uuid4(), event_type=tipo, occurred_at=momento, data=data,
                    status=status, confirmed_at=(confirmado or momento) if status == "confirmado" else None)


def fermentar(momento=T0, **kw) -> LotEvent:
    return ev("beneficio", momento, etapa="despulpado y fermentación", **kw)


def lavar(momento) -> LotEvent:
    return ev("beneficio", momento, etapa="lavado")


def temperatura(momento, grados, **kw) -> LotEvent:
    return ev("medicion_fermentacion", momento, temperatura=(grados, "grado_celsius"), **kw)


def humedad(momento, porcentaje, tipo="medicion_humedad") -> LotEvent:
    return ev(tipo, momento, humedad=(porcentaje, "porcentaje"))


# ---------------------------------------------------------------------------
# Etapa del lote
# ---------------------------------------------------------------------------


def test_sin_eventos_no_hay_etapa(reglas) -> None:
    assert alertas.etapa_del_lote(reglas, []) is None


def test_etapa_sale_del_ultimo_evento_que_la_cambia(reglas) -> None:
    eventos = [fermentar(), ev("volteo", _h(3)), ev("abonada", _h(4), insumo="urea")]
    etapa = alertas.etapa_del_lote(reglas, eventos)
    assert (etapa.clave, etapa.desde) == ("fermentacion", T0)

    eventos.append(lavar(_h(20)))
    assert alertas.etapa_del_lote(reglas, eventos).clave == "secado"
    eventos.append(ev("secado", _h(100), cantidad_seco=(10, "arroba")))
    assert alertas.etapa_del_lote(reglas, eventos) is None


def test_etapa_ignora_tildes_mayusculas_y_conjugaciones(reglas) -> None:
    assert alertas.etapa_del_lote(reglas, [ev("beneficio", T0, etapa="FERMENTADO")]).clave == \
        "fermentacion"


def test_etapa_ignora_eventos_sin_confirmar(reglas) -> None:
    assert alertas.etapa_del_lote(reglas, [fermentar(status="pendiente")]) is None
    assert alertas.etapa_del_lote(reglas, [fermentar(status="rechazado")]) is None


def test_si_un_evento_empieza_dos_etapas_gana_la_de_mas_abajo(reglas) -> None:
    etapa = alertas.etapa_del_lote(reglas, [ev("beneficio", T0, etapa="fermentación y lavado")])
    assert etapa.clave == "secado"


def test_orden_por_momento_no_por_llegada(reglas) -> None:
    # El lavado se contó primero pero pasó después de la fermentación.
    assert alertas.etapa_del_lote(reglas, [lavar(_h(20)), fermentar()]).clave == "secado"


def test_etapa_del_evento_es_la_de_antes_si_el_evento_la_termina(reglas) -> None:
    historia = [lavar(T0), humedad(_h(30), 14)]
    final = humedad(_h(60), 13, tipo="secado")
    historia.append(final)
    assert alertas.etapa_del_lote(reglas, historia) is None
    assert alertas.etapa_del_evento(reglas, historia, final) == "secado"
    assert alertas.etapa_del_evento(reglas, historia, historia[1]) == "secado"


def test_etapa_del_evento_que_empieza_la_etapa(reglas) -> None:
    inicio = fermentar()
    assert alertas.etapa_del_evento(reglas, [inicio], inicio) == "fermentacion"


# ---------------------------------------------------------------------------
# Alertas por valor (reglas puras)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grados,fuera", [(17.9, True), (18, False), (30, False), (40, False),
                                          (40.1, True)])
def test_rango_con_bordes_incluidos(reglas, grados, fuera) -> None:
    regla = reglas.alertas_por_valor[0]
    assert alertas.fuera_de_rango(regla, grados) is fuera


def test_valor_medido(reglas) -> None:
    regla = reglas.alertas_por_valor[0]
    assert alertas.valor_medido(regla, temperatura(T0, 42)) == 42.0
    assert alertas.valor_medido(regla, ev("volteo", T0)) is None  # no lo midió
    assert alertas.valor_medido(regla, ev("volteo", T0, temperatura="caliente")) is None
    assert alertas.valor_medido(regla, ev("volteo", T0, temperatura=True)) is None


def test_valor_en_otra_unidad_no_se_compara(tmp_path, reglas) -> None:
    regla = alertas.ReglaValor(etapa="secado", campo="cantidad", maximo=100, unidad="kg",
                               mensaje="x")
    assert alertas.valor_medido(regla, ev("secado", T0, cantidad=(500, "kg"))) == 500
    assert alertas.valor_medido(regla, ev("secado", T0, cantidad=(500, "arroba"))) is None


def test_solo_maximo(reglas) -> None:
    regla = alertas.ReglaValor(etapa="secado", campo="humedad", maximo=12, mensaje="x")
    assert not alertas.fuera_de_rango(regla, 0)
    assert alertas.fuera_de_rango(regla, 12.1)


def test_mensajes_con_numeros_a_la_colombiana(reglas) -> None:
    lote = type("Lote", (), {"name": "La Esperanza", "code": "ESP-01"})()
    assert alertas.mensaje_por_valor(reglas.alertas_por_valor[1], lote, 12.8) == \
        "Humedad del lote La Esperanza en 12,8%."
    assert alertas.mensaje_por_valor(reglas.alertas_por_valor[0], lote, 42.0) == \
        "Fermentación del lote La Esperanza a 42°C (rango 18 a 40)."
    assert alertas.mensaje_por_tiempo(reglas.alertas_por_tiempo[0], lote) == \
        "El lote La Esperanza lleva más de 12 horas sin volteo."


# ---------------------------------------------------------------------------
# Alertas por tiempo (reglas puras)
# ---------------------------------------------------------------------------


def _vence(reglas, eventos, indice=0):
    return alertas.vencimiento(reglas.alertas_por_tiempo[indice],
                               alertas.etapa_del_lote(reglas, eventos), eventos)


def test_sin_la_etapa_no_hay_plazo(reglas) -> None:
    assert _vence(reglas, []) is None
    assert _vence(reglas, [lavar(T0)]) is None  # está en secado, no en fermentación


def test_plazo_desde_que_entro_a_la_etapa(reglas) -> None:
    assert _vence(reglas, [fermentar()]) == _h(12)


def test_cada_evento_esperado_reinicia_el_plazo(reglas) -> None:
    assert _vence(reglas, [fermentar(), ev("volteo", _h(5)), ev("volteo", _h(9))]) == _h(21)


def test_evento_esperado_de_antes_de_la_etapa_no_cuenta(reglas) -> None:
    assert _vence(reglas, [ev("volteo", T0 - dt.timedelta(days=3)), fermentar()]) == _h(12)


def test_evento_sin_confirmar_no_reinicia_el_plazo(reglas) -> None:
    assert _vence(reglas, [fermentar(), ev("volteo", _h(5), status="pendiente")]) == _h(12)


def test_secado_48_horas(reglas) -> None:
    assert _vence(reglas, [lavar(T0), humedad(_h(10), 11)], indice=1) == _h(58)


def test_evento_sin_hora_cuenta_desde_que_se_confirmo(reglas) -> None:
    # "Hoy pusimos a fermentar" (sin hora) queda a las 00:00; lo confirmó a las 9 a.m.
    medianoche = dt.datetime(2026, 11, 10, 0, 0, tzinfo=ZONA_COLOMBIA)
    nueve = medianoche.replace(hour=9)
    assert _vence(reglas, [fermentar(medianoche, confirmado=nueve)]) == nueve + dt.timedelta(hours=12)


def test_evento_sin_hora_contado_dias_despues_no_pasa_de_ese_dia(reglas) -> None:
    medianoche = dt.datetime(2026, 11, 10, 0, 0, tzinfo=ZONA_COLOMBIA)
    tres_dias = medianoche + dt.timedelta(days=3)
    assert _vence(reglas, [fermentar(medianoche, confirmado=tres_dias)]) == \
        medianoche + dt.timedelta(days=1, hours=12)


# ---------------------------------------------------------------------------
# Sobre la base
# ---------------------------------------------------------------------------


@pytest.fixture
def repos(db_session) -> Repositories:
    return Repositories(db_session)


def _org(repos: Repositories, nombre: str):
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name=nombre, slug=f"{nombre}-{org_id.hex[:8]}")
    finca = repos.farms.add(org_id, name="El Recreo")
    lote = repos.lots.add(org_id, farm_id=finca.id, code="ESP-01", name="La Esperanza")
    return org_id, lote


@pytest.fixture
def org(repos):
    return _org(repos, "a")


def confirmado(repos, org_id, lote, tipo, momento, **datos) -> LotEvent:
    plantilla = ev(tipo, momento, **datos)
    e = repos.events.add(org_id, lot_id=lote.id, event_type=tipo, occurred_at=momento,
                         data=plantilla.data)
    return repos.events.confirm(org_id, e.id)


def _abiertas(repos, org_id):
    repos.session.expire_all()
    return {a.rule_code: a for a in repos.alerts.list_open(org_id)}


@pytest.mark.db
def test_valor_fuera_de_rango_abre_una_alerta_y_no_la_repite(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    caliente = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(2),
                          temperatura=(42, "grado_celsius"))

    [nueva] = alertas.al_confirmar(repos, reglas, org_id, [caliente.id], _h(2))
    alerta = repos.alerts.get(org_id, nueva)
    assert alerta.rule_code == "valor:fermentacion:temperatura"
    assert alerta.status == "pendiente" and alerta.event_id == caliente.id
    assert alerta.message == "Fermentación del lote La Esperanza a 42°C (rango 18 a 40)."
    assert alerta.due_at == _h(2)

    # Otra medición fuera de rango: la alerta sigue abierta, no se repite.
    mas = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(3),
                     temperatura=(45, "grado_celsius"))
    assert alertas.al_confirmar(repos, reglas, org_id, [mas.id], _h(3)) == []
    assert len(_abiertas(repos, org_id)) == 1


@pytest.mark.db
def test_valor_dentro_del_rango_cierra_la_alerta(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    caliente = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(2),
                          temperatura=(42, "grado_celsius"))
    [primera] = alertas.al_confirmar(repos, reglas, org_id, [caliente.id], _h(2))

    bien = confirmado(repos, org_id, lote, "volteo", _h(4), temperatura=(30, "grado_celsius"))
    assert alertas.al_confirmar(repos, reglas, org_id, [bien.id], _h(4)) == []
    assert "valor:fermentacion:temperatura" not in _abiertas(repos, org_id)
    assert repos.alerts.get(org_id, primera).status == "resuelta"

    # Después de cerrada puede volver a saltar.
    otra = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(6),
                      temperatura=(41, "grado_celsius"))
    assert len(alertas.al_confirmar(repos, reglas, org_id, [otra.id], _h(6))) == 1


@pytest.mark.db
def test_medicion_vieja_dentro_del_rango_no_cierra_una_alerta_nueva(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    caliente = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(5),
                          temperatura=(42, "grado_celsius"))
    alertas.al_confirmar(repos, reglas, org_id, [caliente.id], _h(5))
    # Contó tarde una medición de antes.
    vieja = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(1),
                       temperatura=(25, "grado_celsius"))
    alertas.al_confirmar(repos, reglas, org_id, [vieja.id], _h(6))
    assert "valor:fermentacion:temperatura" in _abiertas(repos, org_id)


@pytest.mark.db
def test_valor_fuera_de_la_etapa_de_la_regla_no_alerta(repos, reglas, org) -> None:
    org_id, lote = org
    # Sin fermentación registrada: no se sabe que el lote esté fermentando.
    caliente = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(2),
                          temperatura=(42, "grado_celsius"))
    assert alertas.al_confirmar(repos, reglas, org_id, [caliente.id], _h(2)) == []


@pytest.mark.db
def test_evento_pendiente_no_dispara_alertas(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    pendiente = repos.events.add(org_id, lot_id=lote.id, event_type="medicion_fermentacion",
                                 occurred_at=_h(2),
                                 data={"temperatura": {"valor": 50, "unidad": "grado_celsius"}})
    assert alertas.al_confirmar(repos, reglas, org_id, [pendiente.id], _h(2)) == []
    assert _abiertas(repos, org_id) == {}


@pytest.mark.db
def test_humedad_final_del_secado_alerta_y_queda_abierta(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="lavado")
    final = confirmado(repos, org_id, lote, "secado", _h(50), cantidad_seco=(20, "arroba"),
                       humedad=(14, "porcentaje"))
    [nueva] = alertas.al_confirmar(repos, reglas, org_id, [final.id], _h(50))
    assert repos.alerts.get(org_id, nueva).message == "Humedad del lote La Esperanza en 14%."
    # El secado terminó, pero la alerta por valor sigue abierta (hay que verla).
    alertas.revisar_tiempos(repos, reglas, org_id, _h(200))
    assert set(_abiertas(repos, org_id)) == {"valor:secado:humedad"}


@pytest.mark.db
def test_nueva_tanda_cierra_las_alertas_por_valor_de_la_anterior(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    caliente = confirmado(repos, org_id, lote, "medicion_fermentacion", _h(2),
                          temperatura=(42, "grado_celsius"))
    alertas.al_confirmar(repos, reglas, org_id, [caliente.id], _h(2))
    otra_tanda = confirmado(repos, org_id, lote, "beneficio", _h(72), etapa="fermentación")
    alertas.al_confirmar(repos, reglas, org_id, [otra_tanda.id], _h(72))
    assert "valor:fermentacion:temperatura" not in _abiertas(repos, org_id)


@pytest.mark.db
def test_alerta_por_tiempo_salta_al_vencer_y_no_se_repite(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")

    assert alertas.revisar_tiempos(repos, reglas, org_id, _h(11.9)) == []
    [nueva] = alertas.revisar_tiempos(repos, reglas, org_id, _h(12))
    alerta = repos.alerts.get(org_id, nueva)
    assert alerta.rule_code == "tiempo:fermentacion:volteo"
    assert alerta.message == "El lote La Esperanza lleva más de 12 horas sin volteo."
    assert alerta.due_at == _h(12) and alerta.event_id is None

    # Cada revisión de beat la encuentra abierta: no la repite.
    assert alertas.revisar_tiempos(repos, reglas, org_id, _h(12.5)) == []
    assert alertas.revisar_tiempos(repos, reglas, org_id, _h(30)) == []
    assert len(_abiertas(repos, org_id)) == 1


@pytest.mark.db
def test_llega_el_evento_esperado_y_se_cierra(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    [nueva] = alertas.revisar_tiempos(repos, reglas, org_id, _h(13))

    volteo = confirmado(repos, org_id, lote, "volteo", _h(14))
    # Se cierra en cuanto se confirma, sin esperar a beat.
    assert alertas.al_confirmar(repos, reglas, org_id, [volteo.id], _h(14)) == []
    assert repos.alerts.get(org_id, nueva).status == "resuelta"
    # Y vuelve a saltar 12 horas después del volteo.
    assert alertas.revisar_tiempos(repos, reglas, org_id, _h(25.9)) == []
    assert len(alertas.revisar_tiempos(repos, reglas, org_id, _h(26))) == 1


@pytest.mark.db
def test_salir_de_la_etapa_cierra_la_alerta_por_tiempo(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    alertas.revisar_tiempos(repos, reglas, org_id, _h(13))
    confirmado(repos, org_id, lote, "beneficio", _h(15), etapa="lavado")
    alertas.revisar_tiempos(repos, reglas, org_id, _h(16))
    assert _abiertas(repos, org_id) == {}


@pytest.mark.db
def test_lotes_inactivos_no_se_revisan(repos, reglas, org) -> None:
    org_id, lote = org
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    repos.lots.update(org_id, lote.id, status="inactivo")
    assert alertas.revisar_tiempos(repos, reglas, org_id, _h(100)) == []


@pytest.mark.db
def test_revisar_una_organizacion_no_toca_otra(repos, reglas, org) -> None:
    org_id, lote = org
    otra_id, otro_lote = _org(repos, "b")
    confirmado(repos, org_id, lote, "beneficio", T0, etapa="fermentación")
    confirmado(repos, otra_id, otro_lote, "beneficio", T0, etapa="fermentación")

    assert len(alertas.revisar_tiempos(repos, reglas, org_id, _h(13))) == 1
    assert _abiertas(repos, otra_id) == {}
    # Ids de eventos de otra organización no se leen.
    caliente_b = confirmado(repos, otra_id, otro_lote, "medicion_fermentacion", _h(2),
                            temperatura=(42, "grado_celsius"))
    assert alertas.al_confirmar(repos, reglas, org_id, [caliente_b.id], _h(2)) == []
    assert _abiertas(repos, otra_id) == {}
