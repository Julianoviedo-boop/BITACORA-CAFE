"""Prueba de scripts/registrar_organizacion.py (montado en /scripts dentro del contenedor)."""

import importlib.util
from pathlib import Path

import pytest

from app.db.repositories import Repositories
from app.db.repositories.routing import find_organization_id_by_whatsapp_address

pytestmark = pytest.mark.db

_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "scripts" / "registrar_organizacion.py",
    Path("/scripts/registrar_organizacion.py"),
]


@pytest.fixture(scope="module")
def script():
    path = next((p for p in _CANDIDATES if p.is_file()), None)
    if path is None:
        pytest.skip("No se encontró scripts/registrar_organizacion.py")
    spec = importlib.util.spec_from_file_location("registrar_organizacion", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registra_organizacion_enrutable_con_productor(script, db_session):
    repos = Repositories(db_session)
    r = script.registrar(repos, "1519752507881349", telefono="573001112233")

    assert r.organizacion_creada and r.productor_creado
    assert find_organization_id_by_whatsapp_address(db_session, "1519752507881349") == (
        r.organization_id
    )
    assert repos.producers.get_by_phone(r.organization_id, "+573001112233") is not None
    assert repos.lots.get_by_code(r.organization_id, "PRU-01") is not None


def test_es_idempotente(script, db_session):
    repos = Repositories(db_session)
    a = script.registrar(repos, "1519752507881349", telefono="+573001112233")
    b = script.registrar(repos, "1519752507881349", telefono="+573001112233")

    assert b.organization_id == a.organization_id
    assert not b.organizacion_creada and not b.productor_creado
    assert len(repos.farms.list(a.organization_id)) == 1


def test_agrega_productor_a_organizacion_existente(script, db_session):
    repos = Repositories(db_session)
    a = script.registrar(repos, "1519752507881349")
    b = script.registrar(repos, "1519752507881349", telefono="+573009998877")
    assert a.productor_phone is None
    assert b.organization_id == a.organization_id and b.productor_creado


@pytest.mark.parametrize("numero, telefono", [("abc", None), ("123", "no-es-telefono")])
def test_rechaza_datos_invalidos(script, db_session, numero, telefono):
    with pytest.raises(ValueError):
        script.registrar(Repositories(db_session), numero, telefono=telefono)
