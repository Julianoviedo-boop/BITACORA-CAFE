"""Celery beat (alertas por tiempo) y validación de alertas.yaml al arrancar."""

import datetime as dt
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.repositories import Repositories, unit_of_work
from app.domain import alertas
from app.extraction.schema import cargar_catalogo
from app.worker import tasks
from app.worker.celery_app import celery_app

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


def _config_dir() -> Path:
    d = next((p for p in _CONFIG if (p / "alertas.yaml").is_file()), None)
    if d is None:
        pytest.skip("No se encontró config/alertas.yaml")
    return d


def test_beat_programa_la_revision_de_alertas() -> None:
    programa = celery_app.conf.beat_schedule["revisar-alertas-por-tiempo"]
    assert programa["task"] == "revisar_alertas_por_tiempo"
    assert programa["schedule"] == 30 * 60  # ALERTAS_REVISION_MINUTOS por defecto
    assert programa["options"]["expires"] == programa["schedule"]
    assert "revisar_alertas_por_tiempo" in celery_app.tasks


# ---------------------------------------------------------------------------
# Tarea de beat
# ---------------------------------------------------------------------------


@pytest.fixture
def repos(db_session) -> Repositories:
    return Repositories(db_session)


@pytest.fixture
def beat(db_session, monkeypatch):
    """La tarea corre contra la sesión de la prueba; los envíos se capturan."""
    config_dir = _config_dir()
    reglas = alertas.cargar_reglas(config_dir, cargar_catalogo(config_dir))
    encoladas: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks, "unit_of_work", lambda: unit_of_work(db_session))
    monkeypatch.setattr(alertas, "get_reglas", lambda: reglas)
    monkeypatch.setattr(tasks.enviar_alerta, "delay", lambda *a: encoladas.append(a))
    return encoladas


def _lote_fermentando(repos, horas: float) -> tuple[uuid.UUID, uuid.UUID]:
    """Organización con un lote que entró a fermentación hace `horas` (con hora exacta)."""
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name="Org", slug=f"t-{org_id.hex[:8]}")
    finca = repos.farms.add(org_id, name="El Recreo")
    lote = repos.lots.add(org_id, farm_id=finca.id, code="ESP-01", name="La Esperanza")
    inicio = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=horas)).replace(second=7)
    e = repos.events.add(org_id, lot_id=lote.id, event_type="beneficio", occurred_at=inicio,
                         data={"etapa": {"valor": "fermentación", "unidad": None}})
    repos.events.confirm(org_id, e.id)
    return org_id, lote.id


@pytest.mark.db
def test_revision_abre_las_vencidas_y_las_encola_una_vez(repos, beat) -> None:
    org_id, _ = _lote_fermentando(repos, horas=200)  # pasa cualquier plazo provisional
    otra_id, _ = _lote_fermentando(repos, horas=0.1)  # recién empezó

    resumen = tasks.revisar_alertas_por_tiempo.apply().get()
    assert resumen["alertas_nuevas"] == 1 and resumen["errores"] == 0
    [alerta] = repos.alerts.list_open(org_id)
    assert alerta.rule_code.startswith("tiempo:fermentacion:")
    assert beat == [(str(org_id), str(alerta.id))]
    assert repos.alerts.list_open(otra_id) == []

    # La siguiente revisión la encuentra abierta: no la repite ni la vuelve a mandar.
    assert tasks.revisar_alertas_por_tiempo.apply().get()["alertas_nuevas"] == 0
    assert len(beat) == 1


@pytest.mark.db
def test_si_una_organizacion_falla_las_demas_siguen(repos, beat, monkeypatch) -> None:
    mala, _ = _lote_fermentando(repos, horas=200)
    buena, _ = _lote_fermentando(repos, horas=200)
    original = alertas.revisar_tiempos

    def revisar(repos_, reglas, org_id, ahora):
        if org_id == mala:
            raise RuntimeError("dato dañado")
        return original(repos_, reglas, org_id, ahora)

    monkeypatch.setattr(alertas, "revisar_tiempos", revisar)
    resumen = tasks.revisar_alertas_por_tiempo.apply().get()
    assert resumen["errores"] == 1
    assert len(repos.alerts.list_open(buena)) == 1
    assert repos.alerts.list_open(mala) == []


# ---------------------------------------------------------------------------
# Validación al arrancar
# ---------------------------------------------------------------------------


def _yaml_invalido(monkeypatch) -> None:
    def falla() -> None:
        raise alertas.AlertasInvalidasError("alertas.yaml tiene errores:\n  - campo: falta")

    monkeypatch.setattr(alertas, "validar_al_arrancar", falla)


def test_worker_y_beat_no_arrancan_con_el_yaml_invalido(monkeypatch, caplog) -> None:
    _yaml_invalido(monkeypatch)
    with pytest.raises(SystemExit):
        tasks.validar_configuracion()
    assert "campo: falta" in caplog.text


def test_worker_arranca_con_el_yaml_valido(monkeypatch) -> None:
    monkeypatch.setattr(alertas, "validar_al_arrancar", lambda: None)
    tasks.validar_configuracion()


def test_la_api_no_arranca_con_el_yaml_invalido(monkeypatch) -> None:
    from app.main import app

    _yaml_invalido(monkeypatch)
    with pytest.raises(alertas.AlertasInvalidasError), TestClient(app):
        pass


def test_la_api_arranca_con_el_yaml_real(monkeypatch) -> None:
    from app.main import app

    config_dir = _config_dir()
    monkeypatch.setattr(alertas, "get_reglas",
                        lambda: alertas.cargar_reglas(config_dir, cargar_catalogo(config_dir)))
    with TestClient(app) as client:
        assert client.get("/health").status_code in (200, 503)
