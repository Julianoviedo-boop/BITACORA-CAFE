"""Prueba de scripts/seed.py (montado en /scripts dentro del contenedor)."""

import importlib.util
from pathlib import Path

import pytest

from app.db.repositories import Repositories

pytestmark = pytest.mark.db

_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "scripts" / "seed.py",  # repositorio local
    Path("/scripts/seed.py"),  # contenedor
]


@pytest.fixture(scope="module")
def seed():
    path = next((p for p in _CANDIDATES if p.is_file()), None)
    if path is None:
        pytest.skip("No se encontró scripts/seed.py")
    spec = importlib.util.spec_from_file_location("seed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_crea_dos_organizaciones_y_es_idempotente(seed, db_session):
    repos = Repositories(db_session)
    results = [seed.seed_organization(repos, d) for d in seed.DATOS]
    assert len(results) == 2 and all(created for _, created in results)

    for (org_id, _), data in zip(results, seed.DATOS, strict=True):
        n_lotes = sum(len(f["lotes"]) for f in data["fincas"])
        assert len(repos.farms.list(org_id)) == len(data["fincas"])
        assert len(repos.lots.list(org_id)) == n_lotes

    again = [seed.seed_organization(repos, d) for d in seed.DATOS]
    assert [org for org, _ in again] == [org for org, _ in results]
    assert not any(created for _, created in again)


def test_seed_mismo_codigo_en_dos_organizaciones_no_se_mezcla(seed, db_session):
    repos = Repositories(db_session)
    (org_a, _), (org_b, _) = (seed.seed_organization(repos, d) for d in seed.DATOS)
    lot_a = repos.lots.get_by_code(org_a, "MIR-01")
    lot_b = repos.lots.get_by_code(org_b, "MIR-01")
    assert lot_a.id != lot_b.id
    assert (lot_a.name, lot_b.name) == ("La Esperanza", "El Cedro")
