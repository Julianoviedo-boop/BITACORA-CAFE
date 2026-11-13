"""Comportamiento de los repositorios dentro de una misma organización."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.repositories import (
    InvalidTransitionError,
    ProtectedFieldError,
    Repositories,
    unit_of_work,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 1, 7, 30, tzinfo=UTC)


@pytest.fixture
def repos(db_session: Session) -> Repositories:
    return Repositories(db_session)


@pytest.fixture
def org(repos: Repositories) -> uuid.UUID:
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name="Asociación de prueba", slug=f"prueba-{org_id.hex[:8]}")
    return org_id


@pytest.fixture
def lot(repos: Repositories, org: uuid.UUID):
    farm = repos.farms.add(org, name="El Mirador")
    return repos.lots.add(org, farm_id=farm.id, name="La Esperanza", code="ESP-01")


# --- organizaciones ---

def test_numero_receptor_unico_entre_organizaciones(repos, org, db_session):
    repos.organizations.update(org, whatsapp_address="100000000000099")
    otra = uuid.uuid4()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        repos.organizations.create(otra, name="Otra", slug=f"otra-{otra.hex[:8]}",
                                   whatsapp_address="100000000000099")


def test_organizacion_solo_permite_editar_nombre_y_slug(repos, org):
    assert repos.organizations.update(org, name="Nuevo nombre").name == "Nuevo nombre"
    with pytest.raises(ProtectedFieldError):
        repos.organizations.update(org, id=uuid.uuid4())


# --- productores ---

def test_telefono_unico_por_organizacion(repos, org, db_session):
    repos.producers.add(org, full_name="Ana", phone="+573001234567")
    with pytest.raises(IntegrityError), db_session.begin_nested():
        repos.producers.add(org, full_name="Otra Ana", phone="+573001234567")


def test_consentimiento_queda_con_fecha(repos, org):
    p = repos.producers.add(org, full_name="Ana", phone="+573001234567")
    assert p.consent_status == "pendiente" and p.consent_at is None
    p = repos.producers.record_consent(org, p.id, "aceptado")
    assert p.consent_status == "aceptado" and p.consent_at is not None


def test_consentimiento_solo_cambia_con_record_consent(repos, org):
    p = repos.producers.add(org, full_name="Ana", phone="+573001234567")
    with pytest.raises(ProtectedFieldError):
        repos.producers.update(org, p.id, consent_status="aceptado")
    with pytest.raises(ValueError):
        repos.producers.record_consent(org, p.id, "tal vez")


# --- lotes ---

def test_lote_genera_codigo_legible_si_no_se_da(repos, org):
    farm = repos.farms.add(org, name="El Mirador")
    first = repos.lots.add(org, farm_id=farm.id, name="Uno")
    second = repos.lots.add(org, farm_id=farm.id, name="Dos")
    assert (first.code, second.code) == ("LOTE-001", "LOTE-002")


def test_token_publico_largo_unico_y_regenerable(repos, org, lot):
    other = repos.lots.add(org, farm_id=lot.farm_id, name="Otro")
    assert len(lot.public_token) >= 43
    assert lot.public_token != other.public_token
    old = lot.public_token
    repos.lots.regenerate_public_token(org, lot.id)
    assert lot.public_token != old
    assert repos.lots.get_by_public_token(org, old) is None


def test_token_publico_no_se_asigna_a_mano(repos, org, lot):
    with pytest.raises(ProtectedFieldError):
        repos.lots.update(org, lot.id, public_token="facil")


def test_update_rechaza_campos_inexistentes(repos, org, lot):
    with pytest.raises(AttributeError):
        repos.lots.update(org, lot.id, metadata="x")


# --- mensajes entrantes ---

def test_add_if_new_es_idempotente(repos, org):
    first, created = repos.messages.add_if_new(
        org, "wamid.123", from_phone="+573001234567", message_type="audio", media_ref="media/1"
    )
    again, created_again = repos.messages.add_if_new(
        org, "wamid.123", from_phone="+573001234567", message_type="audio", media_ref="otro"
    )
    assert created and not created_again
    assert again.id == first.id and again.media_ref == "media/1"
    assert len(repos.messages.list(org)) == 1


def test_external_id_no_se_cambia(repos, org):
    msg, _ = repos.messages.add_if_new(org, "wamid.1", from_phone="+57300", message_type="texto")
    with pytest.raises(ProtectedFieldError):
        repos.messages.update(org, msg.id, external_id="wamid.2")


def test_estado_de_procesamiento(repos, org):
    msg, _ = repos.messages.add_if_new(org, "wamid.1", from_phone="+57300", message_type="texto")
    assert msg.processing_status == "recibido"
    msg = repos.messages.set_processing_status(org, msg.id, "error", error_detail="sin audio")
    assert (msg.processing_status, msg.error_detail) == ("error", "sin audio")
    with pytest.raises(ValueError):
        repos.messages.set_processing_status(org, msg.id, "inventado")


def test_estado_con_expected_es_condicional(repos, org):
    msg, _ = repos.messages.add_if_new(org, "wamid.1", from_phone="+57300", message_type="texto")
    tomado = repos.messages.set_processing_status(org, msg.id, "procesado", expected="recibido")
    assert tomado.processing_status == "procesado"
    # Un segundo worker ya no lo encuentra en "recibido".
    assert repos.messages.set_processing_status(org, msg.id, "error", expected="recibido") is None
    assert repos.messages.get(org, msg.id).processing_status == "procesado"
    with pytest.raises(ValueError):
        repos.messages.set_processing_status(org, msg.id, "error", expected="inventado")


def test_tipos_imagen_y_sin_consentimiento(repos, org):
    msg, _ = repos.messages.add_if_new(org, "wamid.img", from_phone="+57300", message_type="imagen",
                                       media_ref="internal://media/" + "x" * 600)
    msg = repos.messages.set_processing_status(org, msg.id, "sin_consentimiento")
    assert (msg.message_type, msg.processing_status) == ("imagen", "sin_consentimiento")


# --- eventos (regla de oro 2) ---

def test_evento_nace_pendiente_y_no_se_confirma_por_add_ni_update(repos, org, lot):
    with pytest.raises(ProtectedFieldError):
        repos.events.add(org, lot_id=lot.id, event_type="abonada", occurred_at=NOW,
                         status="confirmado")
    ev = repos.events.add(org, lot_id=lot.id, event_type="abonada", occurred_at=NOW)
    assert ev.status == "pendiente" and ev.data == {}
    with pytest.raises(ProtectedFieldError):
        repos.events.update(org, ev.id, status="confirmado")


def test_confirmar_y_rechazar(repos, org, lot):
    ev1 = repos.events.add(org, lot_id=lot.id, event_type="abonada", occurred_at=NOW)
    ev2 = repos.events.add(org, lot_id=lot.id, event_type="fumigacion", occurred_at=NOW)
    assert repos.events.confirm(org, ev1.id).confirmed_at is not None
    assert repos.events.reject(org, ev2.id).status == "rechazado"
    with pytest.raises(InvalidTransitionError):
        repos.events.confirm(org, ev2.id)
    with pytest.raises(InvalidTransitionError):
        repos.events.reject(org, ev1.id)
    confirmados = repos.events.list_by_lot(org, lot.id, status="confirmado")
    assert [e.id for e in confirmados] == [ev1.id]


def test_datos_jsonb_ida_y_vuelta(repos, org, lot, db_session):
    data = {"insumo": "17-6-18", "cantidad": 4, "unidad": "bulto", "trabajadores": ["Juan"]}
    ev = repos.events.add(org, lot_id=lot.id, event_type="abonada", occurred_at=NOW, data=data,
                          confidence=0.92)
    db_session.expire_all()
    assert repos.events.get(org, ev.id).data == data


def test_confianza_entre_cero_y_uno(repos, org, lot, db_session):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        repos.events.add(org, lot_id=lot.id, event_type="abonada", occurred_at=NOW,
                         confidence=1.5)


def test_historia_ordenada_por_fecha(repos, org, lot):
    late = repos.events.add(org, lot_id=lot.id, event_type="b", occurred_at=NOW)
    early = repos.events.add(org, lot_id=lot.id, event_type="a",
                             occurred_at=NOW.replace(day=1, hour=1))
    assert [e.id for e in repos.events.list_by_lot(org, lot.id)] == [early.id, late.id]


# --- alertas y reportes ---

def test_alerta_enviada(repos, org, lot):
    alert = repos.alerts.add(org, lot_id=lot.id, rule_code="carencia", message="Espere 15 días")
    repos.alerts.mark_sent(org, alert.id)
    assert alert.status == "enviada" and alert.sent_at is not None
    assert repos.alerts.list_pending(org) == []


def test_alerta_abierta_no_se_repite(repos, org, lot):
    primera = repos.alerts.open_if_absent(org, lot_id=lot.id, rule_code="valor:secado:humedad",
                                          message="Humedad alta", due_at=NOW)
    assert primera is not None and primera.status == "pendiente"
    # Abierta (pendiente o enviada): la misma regla no vuelve a abrir otra.
    assert repos.alerts.open_if_absent(org, lot_id=lot.id, rule_code="valor:secado:humedad",
                                       message="Otra vez") is None
    repos.alerts.mark_sent(org, primera.id)
    assert repos.alerts.open_if_absent(org, lot_id=lot.id, rule_code="valor:secado:humedad",
                                       message="Otra vez") is None
    # Otra regla del mismo lote sí.
    assert repos.alerts.open_if_absent(org, lot_id=lot.id, rule_code="tiempo:secado:x",
                                       message="x") is not None
    assert len(repos.alerts.list_open(org, lot_id=lot.id)) == 2


def test_indice_impide_dos_alertas_abiertas_iguales(repos, org, lot, db_session):
    repos.alerts.add(org, lot_id=lot.id, rule_code="r", message="uno")
    with pytest.raises(IntegrityError), db_session.begin_nested():
        repos.alerts.add(org, lot_id=lot.id, rule_code="r", message="dos")


def test_alerta_resuelta_puede_volver_a_abrirse(repos, org, lot):
    alerta = repos.alerts.open_if_absent(org, lot_id=lot.id, rule_code="r", message="uno")
    assert repos.alerts.resolve_open(org, lot.id, "r") == 1
    assert repos.alerts.get(org, alerta.id).status == "resuelta"
    assert repos.alerts.get(org, alerta.id).resolved_at is not None
    assert repos.alerts.list_open(org) == []
    assert repos.alerts.resolve_open(org, lot.id, "r") == 0
    assert repos.alerts.open_if_absent(org, lot_id=lot.id, rule_code="r", message="dos") is not None


def test_resolver_solo_alertas_anteriores(repos, org, lot):
    repos.alerts.open_if_absent(org, lot_id=lot.id, rule_code="r", message="x", due_at=NOW)
    # Una medición de antes de la alerta (llegó tarde) no la cierra.
    antes = NOW.replace(hour=6)
    assert repos.alerts.resolve_open(org, lot.id, "r", due_until=antes) == 0
    assert repos.alerts.resolve_open(org, lot.id, "r", due_until=NOW) == 1


def test_productores_de_una_finca(repos, org, lot):
    otra_finca = repos.farms.add(org, name="Otra")
    ana = repos.producers.add(org, full_name="Ana", phone="+573001111111", farm_id=lot.farm_id)
    luis = repos.producers.add(org, full_name="Luis", phone="+573002222222", farm_id=lot.farm_id)
    repos.producers.add(org, full_name="Eva", phone="+573003333333", farm_id=otra_finca.id)
    repos.producers.record_consent(org, luis.id, "aceptado")
    assert [p.id for p in repos.producers.list_by_farm(org, lot.farm_id)] == [ana.id, luis.id]
    assert [p.id for p in repos.producers.list_by_farm(
        org, lot.farm_id, consent_status="aceptado")] == [luis.id]


def test_ids_de_organizaciones_para_tareas_programadas(db_session, org):
    from app.db.repositories.routing import list_organization_ids

    assert org in list_organization_ids(db_session)


def test_reporte_rango_de_fechas_valido(repos, org, lot, db_session):
    from datetime import date

    with pytest.raises(IntegrityError), db_session.begin_nested():
        repos.reports.add(org, lot_id=lot.id, date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))


# --- unidad de trabajo ---

def test_unit_of_work_hace_rollback_si_hay_error(db_session):
    org_id = uuid.uuid4()
    with pytest.raises(RuntimeError):
        with unit_of_work(db_session) as repos:
            repos.organizations.create(org_id, name="X", slug=f"x-{org_id.hex[:8]}")
            raise RuntimeError("falla a mitad")
    assert Repositories(db_session).organizations.get(org_id) is None
