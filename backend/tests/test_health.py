from fastapi.testclient import TestClient

from app.api import health
from app.main import app

client = TestClient(app)


def _ok() -> None:
    return None


def _falla() -> None:
    raise ConnectionError("sin conexión")


def test_health_ok(monkeypatch):
    monkeypatch.setattr(health, "check_db", _ok)
    monkeypatch.setattr(health, "check_redis", _ok)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}


def test_health_db_caida(monkeypatch):
    monkeypatch.setattr(health, "check_db", _falla)
    monkeypatch.setattr(health, "check_redis", _ok)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["checks"] == {"db": "error", "redis": "ok"}


def test_health_redis_caido(monkeypatch):
    monkeypatch.setattr(health, "check_db", _ok)
    monkeypatch.setattr(health, "check_redis", _falla)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["checks"] == {"db": "ok", "redis": "error"}
