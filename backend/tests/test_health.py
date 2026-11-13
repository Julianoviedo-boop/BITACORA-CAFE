import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import health
from app.main import app

client = TestClient(app)


def _ok() -> None:
    return None


def _falla() -> None:
    raise ConnectionError("sin conexión")


def _todo_ok(monkeypatch):
    monkeypatch.setattr(health, "check_db", _ok)
    monkeypatch.setattr(health, "check_migraciones", _ok)
    monkeypatch.setattr(health, "check_redis", _ok)


def test_health_ok(monkeypatch):
    _todo_ok(monkeypatch)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"db": "ok", "migraciones": "ok", "redis": "ok"},
    }


def test_health_db_caida(monkeypatch):
    _todo_ok(monkeypatch)
    monkeypatch.setattr(health, "check_db", _falla)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["checks"] == {"db": "error", "migraciones": "ok", "redis": "ok"}


def test_health_redis_caido(monkeypatch):
    _todo_ok(monkeypatch)
    monkeypatch.setattr(health, "check_redis", _falla)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["checks"] == {"db": "ok", "migraciones": "ok", "redis": "error"}


def test_health_migraciones_pendientes(monkeypatch):
    _todo_ok(monkeypatch)
    monkeypatch.setattr(health, "check_migraciones", _falla)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["checks"]["migraciones"] == "error"


def test_alembic_heads_hay_una_sola_cabeza():
    # Dos cabezas significan migraciones en ramas paralelas sin unir.
    assert len(health.alembic_heads()) == 1


def test_verificar_migraciones_base_al_dia(db_session):
    # La base de pruebas se crea con alembic upgrade head.
    health.verificar_migraciones(db_session.connection())


def test_verificar_migraciones_base_atrasada(db_session):
    db_session.execute(text("UPDATE alembic_version SET version_num = 'vieja'"))

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        health.verificar_migraciones(db_session.connection())


def test_verificar_migraciones_sin_tabla_de_alembic(db_session):
    db_session.execute(text("DROP TABLE alembic_version"))

    with pytest.raises(Exception, match="alembic_version"):
        health.verificar_migraciones(db_session.connection())
