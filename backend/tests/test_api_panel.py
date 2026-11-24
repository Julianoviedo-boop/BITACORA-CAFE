"""GET /panel/lotes, /panel/lotes/{id} y /panel/alertas: autenticación y aislamiento."""

import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import panel
from app.config import Settings
from app.db.repositories import Repositories, unit_of_work
from app.domain import historia_lote
from app.domain.conversacion import cargar_textos
from app.extraction.schema import cargar_catalogo
from app.main import app

TOKEN = "t" * 48
ORG_ID = uuid.uuid4()
CONFIG = Path(__file__).resolve().parents[2] / "config"


def _sin_base():
    raise AssertionError("Este caso no debía tocar la base de datos")


@pytest.fixture
def settings(monkeypatch) -> Settings:
    s = Settings(panel_api_token=TOKEN, panel_organization_id=str(ORG_ID))
    monkeypatch.setattr(panel, "get_settings", lambda: s)
    return s


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    app.dependency_overrides[panel.get_unit_of_work] = lambda: _sin_base
    yield TestClient(app)
    app.dependency_overrides.clear()


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- autenticación (sin base) ---


def test_sin_token_no_autoriza(client):
    assert client.get("/panel/lotes").status_code == 401
    assert client.get("/panel/alertas").status_code == 401


def test_token_incorrecto_no_autoriza(client):
    assert client.get("/panel/lotes", headers=_auth("x" * 48)).status_code == 401
    assert client.get("/panel/lotes", headers={"Authorization": TOKEN}).status_code == 401


@pytest.mark.parametrize("token,org", [("", str(ORG_ID)), ("corto", str(ORG_ID)),
                                       (TOKEN, ""), (TOKEN, "no-es-uuid")])
def test_panel_sin_configurar_responde_503(client, settings, token, org):
    settings.panel_api_token = token
    settings.panel_organization_id = org
    assert client.get("/panel/lotes", headers=_auth(token or "x")).status_code == 503


def test_id_de_lote_invalido(client):
    app.dependency_overrides[panel.get_unit_of_work] = lambda: lambda: nullcontext(None)
    assert client.get("/panel/lotes/no-es-uuid", headers=_auth()).status_code == 422


# --- con base de datos ---


@pytest.fixture
def db_client(client, db_session: Session, monkeypatch) -> TestClient:
    monkeypatch.setattr(historia_lote, "catalogo_por_defecto", lambda: cargar_catalogo(CONFIG))
    monkeypatch.setattr(historia_lote, "get_textos", lambda: cargar_textos(CONFIG))
    app.dependency_overrides[panel.get_unit_of_work] = lambda: lambda: unit_of_work(db_session)
    return client


@pytest.fixture
def datos(db_session: Session):
    repos = Repositories(db_session)
    otra = uuid.uuid4()
    for org_id in (ORG_ID, otra):
        if repos.organizations.get(org_id) is None:
            repos.organizations.create(org_id, name="Org", slug=f"p-{org_id.hex[:8]}")
    finca = repos.farms.add(ORG_ID, name="El Mirador")
    lote = repos.lots.add(ORG_ID, farm_id=finca.id, name="La Esperanza", code="ESP-01")
    ev = repos.events.add(ORG_ID, lot_id=lote.id, event_type="volteo",
                          occurred_at=datetime(2026, 11, 2, 14, 30, tzinfo=UTC),
                          data={"temperatura": {"valor": 23, "unidad": "grado_celsius"}})
    repos.events.confirm(ORG_ID, ev.id)
    repos.alerts.open_if_absent(ORG_ID, lot_id=lote.id, rule_code="tiempo:fermentacion:volteo",
                                message="Sin volteo", due_at=datetime(2026, 11, 3, tzinfo=UTC))
    finca_otra = repos.farms.add(otra, name="Ajena")
    lote_otro = repos.lots.add(otra, farm_id=finca_otra.id, name="Ajeno", code="AJ-01")
    repos.alerts.open_if_absent(otra, lot_id=lote_otro.id, rule_code="tiempo:fermentacion:volteo",
                                message="Ajena", due_at=datetime(2026, 11, 3, tzinfo=UTC))
    db_session.flush()
    return {"lote": lote.id, "lote_otro": lote_otro.id}


@pytest.mark.db
def test_lista_solo_lotes_de_la_organizacion_del_token(db_client, datos):
    resp = db_client.get("/panel/lotes", headers=_auth())

    assert resp.status_code == 200
    lotes = resp.json()["lotes"]
    assert [lt["id"] for lt in lotes] == [str(datos["lote"])]
    assert lotes[0]["codigo"] == "ESP-01"
    assert lotes[0]["eventos"] == 1
    assert lotes[0]["ultimo_evento"] == "2026-11-02"
    assert lotes[0]["ultima_actividad"] == "Volteo"
    assert lotes[0]["alertas_abiertas"] == 1
    assert "public_token" not in lotes[0]


@pytest.mark.db
def test_historia_del_lote(db_client, datos):
    resp = db_client.get(f"/panel/lotes/{datos['lote']}", headers=_auth())

    assert resp.status_code == 200
    body = resp.json()
    assert body["lote"]["nombre"] == "La Esperanza"
    [evento] = body["eventos"]
    assert evento["actividad"] == "Volteo"
    assert evento["fecha_texto"] == "lunes 2 de noviembre de 2026"
    assert evento["hora"] == "9:30 a.m."
    assert evento["datos"] == [{"etiqueta": "Temperatura", "valor": "23 °C"}]
    assert evento["estado"] == "confirmado"
    [serie] = body["mediciones"]
    assert (serie["etiqueta"], serie["unidad"]) == ("Temperatura", "°C")
    assert [p["valor"] for p in serie["puntos"]] == [23.0]
    assert [a["mensaje"] for a in body["alertas"]] == ["Sin volteo"]


@pytest.mark.db
def test_lote_de_otra_organizacion_da_404(db_client, datos):
    resp = db_client.get(f"/panel/lotes/{datos['lote_otro']}", headers=_auth())
    assert resp.status_code == 404
    assert db_client.get(f"/panel/lotes/{uuid.uuid4()}", headers=_auth()).status_code == 404


@pytest.mark.db
def test_alertas_solo_de_la_organizacion_del_token(db_client, datos):
    resp = db_client.get("/panel/alertas", headers=_auth())

    assert resp.status_code == 200
    [alerta] = resp.json()["alertas"]
    assert alerta["mensaje"] == "Sin volteo"
    assert alerta["lote_codigo"] == "ESP-01"
    assert alerta["clase"] == "tiempo"
    assert alerta["estado"] == "pendiente"
