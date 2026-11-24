"""Historia del lote para el panel y la página pública (app/domain/historia_lote.py)."""

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import LotEvent
from app.db.repositories import Repositories
from app.domain import historia_lote as historia
from app.domain.alertas import cargar_reglas
from app.domain.conversacion import cargar_textos
from app.extraction.prompts import ZONA_COLOMBIA
from app.extraction.schema import cargar_catalogo, etiqueta_por_defecto

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


@pytest.fixture(scope="module")
def config_dir() -> Path:
    d = next((p for p in _CONFIG if (p / "eventos.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/eventos.yaml")
    return d


@pytest.fixture(scope="module")
def catalogo(config_dir):
    return cargar_catalogo(config_dir)


@pytest.fixture(scope="module")
def textos(config_dir):
    return cargar_textos(config_dir)


@pytest.fixture(scope="module")
def reglas(config_dir, catalogo):
    return cargar_reglas(config_dir, catalogo)


def _evento(tipo: str, momento: datetime, data: dict, texto: str | None = None,
            status: str = "confirmado") -> LotEvent:
    return LotEvent(id=uuid.uuid4(), organization_id=uuid.uuid4(), lot_id=uuid.uuid4(),
                    event_type=tipo, occurred_at=momento, data=data, original_text=texto,
                    status=status)


# --- formato (sin base de datos) ---


def test_etiqueta_por_defecto():
    assert etiqueta_por_defecto("cantidad_cereza") == "Cantidad cereza"


def test_catalogo_trae_etiquetas(catalogo):
    campo = catalogo.tipos["beneficio"].campos["horas_fermentacion"]
    assert campo.nombre_visible() == "Horas de fermentación"


def test_fecha_larga():
    assert historia.fecha_larga(date(2026, 11, 3)) == "martes 3 de noviembre de 2026"


def test_evento_con_temperatura_y_hora(catalogo, textos):
    momento = datetime(2026, 11, 3, 7, 30, tzinfo=ZONA_COLOMBIA)
    ev = _evento("medicion_fermentacion", momento,
                 {"temperatura": {"valor": 24.5, "unidad": "grado_celsius"}},
                 texto="el café está a 24 y medio")

    v = historia.evento_visible(ev, catalogo, textos)

    assert v.actividad == "Medición de la fermentación"
    assert v.fecha == date(2026, 11, 3)
    assert v.fecha_texto == "martes 3 de noviembre de 2026"
    assert v.hora == "7:30 a.m."
    assert [(d.etiqueta, d.valor) for d in v.datos] == [("Temperatura", "24,5 °C")]
    assert v.texto_original == "el café está a 24 y medio"
    assert v.estado == "confirmado"


def test_evento_sin_hora_y_datos_en_orden_del_catalogo(catalogo, textos):
    # Guardado en UTC: la fecha y la hora se muestran en hora de Colombia.
    momento = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)  # 00:00 en Colombia
    ev = _evento("abonada", momento, {
        "jornales": {"valor": 2, "unidad": "jornal"},
        "cantidad": {"valor": 4, "unidad": "bulto"},
        "insumo": {"valor": "17-6-18", "unidad": None},
    })

    v = historia.evento_visible(ev, catalogo, textos)

    assert v.hora is None
    assert v.fecha == date(2026, 11, 3)
    assert [(d.etiqueta, d.valor) for d in v.datos] == [
        ("Abono", "17-6-18"), ("Cantidad", "4 bultos"), ("Jornales", "2 jornales")]


def test_tipo_o_campo_que_ya_no_esta_en_el_catalogo(catalogo, textos):
    momento = datetime(2026, 11, 3, 9, 0, tzinfo=ZONA_COLOMBIA)
    ev = _evento("poda_vieja", momento,
                 {"arboles_podados": {"valor": 120, "unidad": None}, "nota": "texto suelto"})

    v = historia.evento_visible(ev, catalogo, textos)

    assert v.actividad == "Poda vieja"
    assert [(d.etiqueta, d.valor) for d in v.datos] == [
        ("Arboles podados", "120"), ("Nota", "texto suelto")]


def test_fecha_corta():
    assert historia.fecha_corta(date(2026, 11, 3)) == "3 nov"


def test_catalogo_marca_graficas_y_datos_privados(catalogo):
    assert catalogo.tipos["volteo"].campos["temperatura"].grafica is True
    assert catalogo.tipos["abonada"].campos["insumo"].grafica is False
    assert catalogo.tipos["abonada"].campos["jornales"].publico is False
    assert catalogo.tipos["abonada"].campos["insumo"].publico is True
    assert catalogo.tipos["venta"].publico is False
    assert catalogo.tipos["abonada"].publico is True


def test_mediciones_juntan_el_mismo_dato_de_varios_tipos(catalogo, textos):
    eventos = [
        _evento("medicion_fermentacion", datetime(2026, 11, 3, 9, tzinfo=ZONA_COLOMBIA),
                {"temperatura": {"valor": 30, "unidad": "grado_celsius"}}),
        _evento("volteo", datetime(2026, 11, 2, 9, tzinfo=ZONA_COLOMBIA),
                {"temperatura": {"valor": 24.5, "unidad": "grado_celsius"}}),
        # Pendiente: no se grafica (regla de oro 2).
        _evento("volteo", datetime(2026, 11, 4, 9, tzinfo=ZONA_COLOMBIA),
                {"temperatura": {"valor": 99, "unidad": "grado_celsius"}}, status="pendiente"),
        # Dato que no va en la gráfica.
        _evento("abonada", datetime(2026, 11, 1, 9, tzinfo=ZONA_COLOMBIA),
                {"cantidad": {"valor": 4, "unidad": "bulto"}}),
        _evento("medicion_humedad", datetime(2026, 11, 5, 9, tzinfo=ZONA_COLOMBIA),
                {"humedad": {"valor": 11.5, "unidad": "porcentaje"}}),
    ]

    series = historia.mediciones(eventos, catalogo, textos)

    # En el orden de eventos.yaml: la humedad aparece primero (en secado).
    assert [(s.etiqueta, s.unidad) for s in series] == [("Humedad", "%"), ("Temperatura", "°C")]
    temp = next(s for s in series if s.campo == "temperatura")
    assert [p.valor for p in temp.puntos] == [24.5, 30.0]
    assert [p.fecha_texto for p in temp.puntos] == ["2 nov", "3 nov"]


def test_mediciones_ignoran_valores_que_no_son_numeros(catalogo, textos):
    ev = _evento("volteo", datetime(2026, 11, 2, 9, tzinfo=ZONA_COLOMBIA),
                 {"temperatura": {"valor": "caliente", "unidad": None}})
    assert historia.mediciones([ev], catalogo, textos) == []


def test_datos_publicos_quitan_lo_privado(catalogo, textos):
    ev = _evento("abonada", datetime(2026, 11, 3, 9, tzinfo=ZONA_COLOMBIA), {
        "insumo": {"valor": "17-6-18", "unidad": None},
        "jornales": {"valor": 2, "unidad": "jornal"},
        "dato_viejo": "ya no está en el catálogo",
    })
    publicos = historia._datos(ev, catalogo, textos, solo_publicos=True)
    # Cantidad es obligatoria: si falta, se dice; nunca se estima.
    assert [(d.etiqueta, d.valor) for d in publicos] == [
        ("Abono", "17-6-18"), ("Cantidad", "No registrado")]


def test_nombre_de_la_etapa(reglas):
    from app.domain.alertas import nombre_etapa
    assert nombre_etapa(reglas, "fermentacion") == "Fermentación"
    assert nombre_etapa(reglas, "no_existe") == "No existe"


# --- con base de datos ---


@pytest.fixture
def repos(db_session: Session) -> Repositories:
    return Repositories(db_session)


def _org(repos: Repositories) -> uuid.UUID:
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name="Asociación", slug=f"h-{org_id.hex[:8]}")
    return org_id


def _confirmado(repos, org_id, lot_id, tipo, momento, data) -> LotEvent:
    ev = repos.events.add(org_id, lot_id=lot_id, event_type=tipo, occurred_at=momento, data=data)
    repos.events.confirm(org_id, ev.id)
    return ev


@pytest.mark.db
def test_historia_en_orden_con_el_estado_de_cada_evento(repos, catalogo, textos, reglas):
    org = _org(repos)
    finca = repos.farms.add(org, name="El Mirador")
    lote = repos.lots.add(org, farm_id=finca.id, name="La Esperanza", code="ESP-01",
                          variety="Castillo")
    tarde = _confirmado(repos, org, lote.id, "secado", datetime(2026, 11, 10, 15, tzinfo=UTC),
                        {"cantidad_seco": {"valor": 3, "unidad": "arroba"}})
    temprano = _confirmado(repos, org, lote.id, "volteo", datetime(2026, 11, 2, 14, tzinfo=UTC),
                           {"temperatura": {"valor": 22, "unidad": "grado_celsius"}})
    repos.events.add(org, lot_id=lote.id, event_type="abonada",
                     occurred_at=datetime(2026, 11, 5, tzinfo=UTC), data={})  # pendiente
    rechazado = repos.events.add(org, lot_id=lote.id, event_type="desyerba",
                                 occurred_at=datetime(2026, 11, 6, tzinfo=UTC), data={})
    repos.events.reject(org, rechazado.id)

    h = historia.historia_lote(repos, org, lote.id, catalogo=catalogo, textos=textos,
                               reglas=reglas)

    assert h is not None
    assert [(e.tipo, e.estado) for e in h.eventos] == [
        ("volteo", "confirmado"), ("abonada", "pendiente"), ("desyerba", "rechazado"),
        ("secado", "confirmado")]
    assert h.eventos[0].id == temprano.id and h.eventos[-1].id == tarde.id
    assert h.eventos[0].datos[0].valor == "22 °C"
    assert h.lote.finca == "El Mirador"
    assert h.lote.variedad == "Castillo"
    # Solo cuentan los confirmados.
    assert h.lote.eventos == 2
    assert h.lote.ultimo_evento == date(2026, 11, 10)
    assert h.lote.ultima_actividad == "Secado"
    assert [(s.campo, [p.valor for p in s.puntos]) for s in h.mediciones] == [
        ("temperatura", [22.0])]


@pytest.mark.db
def test_historia_de_lote_de_otra_organizacion_no_se_ve(repos, catalogo, textos, reglas):
    org_a, org_b = _org(repos), _org(repos)
    finca = repos.farms.add(org_a, name="Finca A")
    lote = repos.lots.add(org_a, farm_id=finca.id, name="Lote A", code="A-01")

    assert historia.historia_lote(repos, org_b, lote.id, catalogo=catalogo, textos=textos,
                                  reglas=reglas) is None
    assert historia.listar_lotes(repos, org_b, catalogo=catalogo, reglas=reglas) == []
    assert historia.alertas_abiertas(repos, org_b) == []
    # El token público de un lote no sirve con otra organización.
    assert historia.historia_publica(repos, org_b, lote.public_token,
                                     catalogo=catalogo, textos=textos) is None


@pytest.mark.db
def test_listar_lotes_con_resumen(repos, catalogo, reglas):
    org = _org(repos)
    finca = repos.farms.add(org, name="El Mirador")
    viejo = repos.lots.add(org, farm_id=finca.id, name="Viejo", code="A-01", status="inactivo")
    lote2 = repos.lots.add(org, farm_id=finca.id, name="Dos", code="B-02")
    lote1 = repos.lots.add(org, farm_id=finca.id, name="Uno", code="B-01")
    _confirmado(repos, org, lote1.id, "volteo", datetime(2026, 11, 2, 14, tzinfo=UTC), {})
    _confirmado(repos, org, lote1.id, "volteo", datetime(2026, 11, 4, 14, tzinfo=UTC), {})
    repos.events.add(org, lot_id=lote2.id, event_type="volteo",
                     occurred_at=datetime(2026, 11, 4, tzinfo=UTC), data={})  # pendiente

    lotes = historia.listar_lotes(repos, org, catalogo=catalogo, reglas=reglas)

    # Primero los activos, por código; los inactivos al final.
    assert [lt.id for lt in lotes] == [lote1.id, lote2.id, viejo.id]
    assert (lotes[0].eventos, lotes[0].ultimo_evento) == (2, date(2026, 11, 4))
    assert lotes[0].ultimo_evento_texto == "miércoles 4 de noviembre de 2026"
    assert (lotes[1].eventos, lotes[1].ultimo_evento) == (0, None)
    assert lotes[0].finca == "El Mirador"
    assert lotes[2].activo is False
    assert lotes[0].ultima_actividad == "Volteo"
    assert lotes[1].ultima_actividad is None


@pytest.mark.db
def test_etapa_y_alertas_abiertas_en_la_lista(repos, catalogo, reglas):
    org = _org(repos)
    finca = repos.farms.add(org, name="El Mirador")
    fermentando = repos.lots.add(org, farm_id=finca.id, name="Uno", code="C-01")
    sin_etapa = repos.lots.add(org, farm_id=finca.id, name="Dos", code="C-02")
    _confirmado(repos, org, fermentando.id, "beneficio", datetime(2026, 11, 2, 14, tzinfo=UTC),
                {"etapa": {"valor": "despulpado y fermentación", "unidad": None}})
    _confirmado(repos, org, sin_etapa.id, "abonada", datetime(2026, 11, 2, 14, tzinfo=UTC),
                {"insumo": {"valor": "urea", "unidad": None}})
    repos.alerts.open_if_absent(org, lot_id=fermentando.id, rule_code="tiempo:fermentacion:volteo",
                                message="Lleva 12 horas sin volteo",
                                due_at=datetime(2026, 11, 3, 2, tzinfo=UTC))
    cerrada = repos.alerts.open_if_absent(org, lot_id=sin_etapa.id, rule_code="valor:secado:humedad",
                                          message="Humedad alta")
    repos.alerts.resolve_open(org, sin_etapa.id, cerrada.rule_code)

    lotes = {lt.codigo: lt for lt in historia.listar_lotes(repos, org, catalogo=catalogo,
                                                            reglas=reglas)}

    assert (lotes["C-01"].etapa, lotes["C-01"].alertas_abiertas) == ("Fermentación", 1)
    assert (lotes["C-02"].etapa, lotes["C-02"].alertas_abiertas) == (None, 0)


@pytest.mark.db
def test_alertas_abiertas_de_la_organizacion(repos):
    org = _org(repos)
    finca = repos.farms.add(org, name="El Mirador")
    lote = repos.lots.add(org, farm_id=finca.id, name="La Esperanza", code="D-01")
    vieja = repos.alerts.open_if_absent(
        org, lot_id=lote.id, rule_code="tiempo:fermentacion:volteo", message="Sin volteo",
        due_at=datetime(2026, 11, 3, 2, tzinfo=UTC))
    nueva = repos.alerts.open_if_absent(
        org, lot_id=lote.id, rule_code="valor:fermentacion:temperatura", message="42 °C",
        due_at=datetime(2026, 11, 4, 15, tzinfo=UTC))
    repos.alerts.mark_sent(org, nueva.id)

    alertas = historia.alertas_abiertas(repos, org)

    assert [a.id for a in alertas] == [nueva.id, vieja.id]
    assert (alertas[0].clase, alertas[0].estado) == ("valor", "enviada")
    assert (alertas[1].clase, alertas[1].estado) == ("tiempo", "pendiente")
    assert alertas[0].lote_codigo == "D-01"
    assert alertas[0].desde_texto == "miércoles 4 de noviembre de 2026, 10:00 a.m."


@pytest.mark.db
def test_historia_publica_solo_lo_que_puede_ver_un_comprador(repos, catalogo, textos):
    org = _org(repos)
    finca = repos.farms.add(org, name="El Mirador", municipality="Pitalito",
                            department="Huila", altitude_m=1650)
    lote = repos.lots.add(org, farm_id=finca.id, name="La Esperanza", code="E-01",
                          variety="Castillo", process="lavado")
    repos.producers.add(org, farm_id=finca.id, full_name="Rosa Elena", phone="+573100000000")
    abonada = repos.events.add(
        org, lot_id=lote.id, event_type="abonada", occurred_at=datetime(2026, 11, 2, 14, tzinfo=UTC),
        original_text="hoy le echamos 4 bultos con don Pedro",
        data={"insumo": {"valor": "17-6-18", "unidad": None},
              "jornales": {"valor": 2, "unidad": "jornal"}})
    repos.events.confirm(org, abonada.id)
    _confirmado(repos, org, lote.id, "venta", datetime(2026, 11, 9, 14, tzinfo=UTC),
                {"precio": {"valor": 2000000, "unidad": "peso_por_carga"}})
    _confirmado(repos, org, lote.id, "medicion_humedad", datetime(2026, 11, 8, 14, tzinfo=UTC),
                {"humedad": {"valor": 11, "unidad": "porcentaje"}})
    repos.events.add(org, lot_id=lote.id, event_type="desyerba",
                     occurred_at=datetime(2026, 11, 5, tzinfo=UTC), data={})  # pendiente

    h = historia.historia_publica(repos, org, lote.public_token, catalogo=catalogo, textos=textos)

    assert h is not None
    assert h.lote.nombre == "La Esperanza"
    assert (h.lote.municipio, h.lote.departamento, h.lote.altitud_m) == ("Pitalito", "Huila", 1650)
    assert [e.tipo for e in h.eventos] == ["abonada", "medicion_humedad"]
    assert [(d.etiqueta, d.valor) for d in h.eventos[0].datos] == [
        ("Abono", "17-6-18"), ("Cantidad", "No registrado")]
    assert [s.campo for s in h.mediciones] == ["humedad"]
    # Nada de texto original, teléfonos ni nombres de personas.
    plano = repr(h)
    for privado in ("don Pedro", "Rosa Elena", "+5731", "2000000", "jornal"):
        assert privado not in plano
    assert historia.historia_publica(repos, org, "no-existe", catalogo=catalogo,
                                     textos=textos) is None
