"""Historia del lote para el panel (app/domain/historia_lote.py)."""

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import LotEvent
from app.db.repositories import Repositories
from app.domain import historia_lote as historia
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


def _evento(tipo: str, momento: datetime, data: dict, texto: str | None = None) -> LotEvent:
    return LotEvent(id=uuid.uuid4(), organization_id=uuid.uuid4(), lot_id=uuid.uuid4(),
                    event_type=tipo, occurred_at=momento, data=data, original_text=texto,
                    status="confirmado")


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
def test_historia_en_orden_y_solo_confirmados(repos, catalogo, textos):
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

    h = historia.historia_lote(repos, org, lote.id, catalogo=catalogo, textos=textos)

    assert h is not None
    assert [e.id for e in h.eventos] == [temprano.id, tarde.id]
    assert h.eventos[0].datos[0].valor == "22 °C"
    assert h.lote.finca == "El Mirador"
    assert h.lote.variedad == "Castillo"
    assert h.lote.eventos == 2
    assert h.lote.ultimo_evento == date(2026, 11, 10)


@pytest.mark.db
def test_historia_de_lote_de_otra_organizacion_no_se_ve(repos, catalogo, textos):
    org_a, org_b = _org(repos), _org(repos)
    finca = repos.farms.add(org_a, name="Finca A")
    lote = repos.lots.add(org_a, farm_id=finca.id, name="Lote A", code="A-01")

    assert historia.historia_lote(repos, org_b, lote.id, catalogo=catalogo, textos=textos) is None
    assert historia.listar_lotes(repos, org_b) == []


@pytest.mark.db
def test_listar_lotes_con_resumen(repos):
    org = _org(repos)
    finca = repos.farms.add(org, name="El Mirador")
    viejo = repos.lots.add(org, farm_id=finca.id, name="Viejo", code="A-01", status="inactivo")
    lote2 = repos.lots.add(org, farm_id=finca.id, name="Dos", code="B-02")
    lote1 = repos.lots.add(org, farm_id=finca.id, name="Uno", code="B-01")
    _confirmado(repos, org, lote1.id, "volteo", datetime(2026, 11, 2, 14, tzinfo=UTC), {})
    _confirmado(repos, org, lote1.id, "volteo", datetime(2026, 11, 4, 14, tzinfo=UTC), {})
    repos.events.add(org, lot_id=lote2.id, event_type="volteo",
                     occurred_at=datetime(2026, 11, 4, tzinfo=UTC), data={})  # pendiente

    lotes = historia.listar_lotes(repos, org)

    # Primero los activos, por código; los inactivos al final.
    assert [lt.id for lt in lotes] == [lote1.id, lote2.id, viejo.id]
    assert (lotes[0].eventos, lotes[0].ultimo_evento) == (2, date(2026, 11, 4))
    assert lotes[0].ultimo_evento_texto == "miércoles 4 de noviembre de 2026"
    assert (lotes[1].eventos, lotes[1].ultimo_evento) == (0, None)
    assert lotes[0].finca == "El Mirador"
    assert lotes[2].activo is False
