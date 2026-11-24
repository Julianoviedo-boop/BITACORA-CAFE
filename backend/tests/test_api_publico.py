"""GET /publico/lotes/{token}: página pública del lote, sin clave."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import publico
from app.db.repositories import Repositories, unit_of_work
from app.db.repositories.routing import find_organization_id_by_public_token
from app.domain import historia_lote
from app.domain.conversacion import cargar_textos
from app.extraction.schema import cargar_catalogo
from app.main import app

CONFIG = Path(__file__).resolve().parents[2] / "config"


def _sin_base():
    raise AssertionError("Este caso no debía tocar la base de datos")


@pytest.fixture
def client():
    app.dependency_overrides[publico.get_unit_of_work] = lambda: _sin_base
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize("token", ["corto", "con espacios y más de veinte letras", "x" * 65,
                                   "%27%20OR%201=1--aaaaaaaaaaaa"])
def test_token_con_forma_invalida_da_404_sin_tocar_la_base(client, token):
    assert client.get(f"/publico/lotes/{token}").status_code == 404


@pytest.fixture
def db_client(client, db_session: Session, monkeypatch) -> TestClient:
    monkeypatch.setattr(historia_lote, "catalogo_por_defecto", lambda: cargar_catalogo(CONFIG))
    monkeypatch.setattr(historia_lote, "get_textos", lambda: cargar_textos(CONFIG))
    app.dependency_overrides[publico.get_unit_of_work] = lambda: lambda: unit_of_work(db_session)
    return client


@pytest.fixture
def lote(db_session: Session):
    repos = Repositories(db_session)
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name="Asociación del Sur", slug=f"pub-{org_id.hex[:8]}")
    finca = repos.farms.add(org_id, name="El Mirador", municipality="Pitalito", department="Huila")
    lote = repos.lots.add(org_id, farm_id=finca.id, name="La Esperanza", code="ESP-01")
    ev = repos.events.add(org_id, lot_id=lote.id, event_type="volteo",
                          occurred_at=datetime(2026, 11, 2, 14, 30, tzinfo=UTC),
                          original_text="lo volteamos con Jairo",
                          data={"temperatura": {"valor": 23, "unidad": "grado_celsius"}})
    repos.events.confirm(org_id, ev.id)
    db_session.flush()
    return {"org": org_id, "token": lote.public_token}


@pytest.mark.db
def test_historia_publica_por_token(db_client, lote):
    resp = db_client.get(f"/publico/lotes/{lote['token']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["lote"]["nombre"] == "La Esperanza"
    assert body["lote"]["organizacion"] == "Asociación del Sur"
    assert [e["actividad"] for e in body["eventos"]] == ["Volteo"]
    assert "Jairo" not in resp.text
    assert "texto_original" not in resp.text
    assert "id" not in body["lote"]


@pytest.mark.db
def test_token_que_no_existe_da_404(db_client, lote):
    assert db_client.get("/publico/lotes/" + "A" * 43).status_code == 404


@pytest.mark.db
def test_buscar_organizacion_por_token(db_session, lote):
    assert find_organization_id_by_public_token(db_session, lote["token"]) == lote["org"]
    assert find_organization_id_by_public_token(db_session, "no-existe") is None
    assert find_organization_id_by_public_token(db_session, "") is None
