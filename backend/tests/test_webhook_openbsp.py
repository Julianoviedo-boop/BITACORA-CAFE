"""POST /webhooks/openbsp/{secreto} y primer paso del worker (consentimiento).

Casos pedidos: credencial inválida, duplicado, mensaje propio, número desconocido
y productor sin consentimiento.
"""

import logging
import random
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import webhooks
from app.channels import get_channel
from app.channels.fake import FakeChannel
from app.config import Settings
from app.db.models import InboundMessage
from app.db.repositories import Repositories, unit_of_work
from app.db.repositories.routing import find_organization_id_by_whatsapp_address
from app.domain.consentimiento import atender_mensaje, cargar_textos
from app.main import app

SECRETO = "s" * 48
TOKEN = "token-del-webhook-en-openbsp"
TELEFONO = "+573101234501"

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Cola:
    def __init__(self) -> None:
        self.encolados: list[tuple[uuid.UUID, uuid.UUID]] = []

    def __call__(self, organization_id: uuid.UUID, message_id: uuid.UUID) -> None:
        self.encolados.append((organization_id, message_id))


@pytest.fixture
def settings(monkeypatch) -> Settings:
    s = Settings(openbsp_webhook_secret=SECRETO, openbsp_webhook_token="")
    monkeypatch.setattr(webhooks, "get_settings", lambda: s)
    return s


@pytest.fixture
def canal(tmp_path) -> FakeChannel:
    return FakeChannel(tmp_path)


@pytest.fixture
def cola() -> Cola:
    return Cola()


def _sin_base():
    raise AssertionError("Este caso no debía tocar la base de datos")


@pytest.fixture
def client(settings, canal, cola) -> Iterator[TestClient]:
    app.dependency_overrides[get_channel] = lambda: canal
    app.dependency_overrides[webhooks.get_encolar] = lambda: cola
    app.dependency_overrides[webhooks.get_unit_of_work] = lambda: _sin_base
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def db_client(client, db_session: Session) -> TestClient:
    app.dependency_overrides[webhooks.get_unit_of_work] = lambda: lambda: unit_of_work(db_session)
    return client


@pytest.fixture
def repos(db_session: Session) -> Repositories:
    return Repositories(db_session)


def _numero() -> str:
    return str(random.randint(10**14, 10**15 - 1))


@pytest.fixture
def org(repos):
    org_id = uuid.uuid4()
    numero = _numero()
    repos.organizations.create(org_id, name="Asocafé Huila", slug=f"t-{org_id.hex[:8]}",
                               whatsapp_address=numero)
    producer = repos.producers.add(org_id, full_name="Rosa Elena Muñoz", phone=TELEFONO)
    repos.session.flush()
    return {"id": org_id, "numero": numero, "producer_id": producer.id}


@pytest.fixture(scope="module")
def textos():
    config_dir = next((p for p in _CONFIG if (p / "consentimiento.yaml").is_file()), None)
    if config_dir is None:
        pytest.skip("No se encontró config/consentimiento.yaml")
    return cargar_textos(config_dir)


def _payload(numero="100000000000001", texto="Hoy abonamos el lote", external_id=None,
             sender=TELEFONO.removeprefix("+"), content=None):
    return {
        "entity": "messages",
        "action": "insert",
        "data": {
            "id": str(uuid.uuid4()),
            "external_id": external_id or f"wamid.{uuid.uuid4().hex}",
            "service": "whatsapp",
            "organization_address": numero,
            "conversation_address": sender or TELEFONO.removeprefix("+"),
            "sender_address": sender,
            "content": content or {"version": "1", "type": "text", "kind": "text", "text": texto},
            "timestamp": "2026-11-12T12:00:00Z",
        },
    }


def _post(client, payload, secreto=SECRETO, headers=None):
    return client.post(f"/webhooks/openbsp/{secreto}", json=payload, headers=headers or {})


def _mensajes(db_session, org_id):
    stmt = select(InboundMessage).where(InboundMessage.organization_id == org_id)
    return db_session.scalars(stmt).all()


# ---------------------------------------------------------------------------
# Credencial inválida (no toca la base: el override de la base falla si se usa)
# ---------------------------------------------------------------------------


def test_secreto_incorrecto(client, cola):
    resp = _post(client, _payload(), secreto="x" * 48)
    assert resp.status_code == 401
    assert cola.encolados == []


def test_secreto_con_un_caracter_distinto(client):
    assert _post(client, _payload(), secreto=SECRETO[:-1] + "t").status_code == 401


def test_sin_secreto_en_la_url_no_existe(client):
    assert client.post("/webhooks/openbsp", json=_payload()).status_code in (404, 405)


@pytest.mark.parametrize("configurado", ["", "corto"])
def test_secreto_no_configurado_o_corto_rechaza_todo(client, settings, configurado):
    settings.openbsp_webhook_secret = configurado
    assert _post(client, _payload(), secreto=configurado or "x").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer otro"}, {"Authorization": TOKEN},
     {"Authorization": f"Basic {TOKEN}"}],
    ids=["sin_header", "token_errado", "sin_bearer", "basic"],
)
def test_con_token_configurado_exige_bearer(client, settings, headers):
    settings.openbsp_webhook_token = TOKEN
    assert _post(client, _payload(), headers=headers).status_code == 401


def test_credencial_invalida_no_revela_formato(client):
    resp = client.post("/webhooks/openbsp/malo", content=b"esto no es json")
    assert resp.status_code == 401


def test_json_invalido_con_credencial(client):
    resp = client.post(f"/webhooks/openbsp/{SECRETO}", content=b"{no json")
    assert resp.status_code == 400


def test_secreto_no_queda_en_el_log_de_acceso():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "POST", f"/webhooks/openbsp/{SECRETO}?a=1", "1.1", 200), None,
    )
    webhooks.OcultarSecretoWebhook().filter(record)
    assert SECRETO not in record.getMessage()
    assert "/webhooks/openbsp/***?a=1" in record.getMessage()


# ---------------------------------------------------------------------------
# Mensaje propio y avisos que no son mensajes (no tocan la base)
# ---------------------------------------------------------------------------


def test_mensaje_propio_se_ignora(client, cola):
    resp = _post(client, _payload(sender=None))
    assert resp.status_code == 200 and resp.json() == {"resultado": "ignorado"}
    assert cola.encolados == []


def test_cambio_de_estado_se_ignora(client, cola):
    resp = _post(client, {**_payload(), "action": "update"})
    assert resp.json() == {"resultado": "ignorado"} and cola.encolados == []


def test_con_bearer_correcto_pasa(client, settings):
    settings.openbsp_webhook_token = TOKEN
    resp = _post(client, _payload(sender=None), headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Producción: se exigen las dos credenciales (URL + token del header)
# ---------------------------------------------------------------------------

TOKEN_LARGO = "t" * 40


@pytest.mark.parametrize("token", ["", "corto"], ids=["sin_token", "token_corto"])
def test_en_produccion_sin_token_rechaza_aunque_la_url_sea_correcta(client, settings, token):
    # Quien solo conozca la URL no puede hacerse pasar por un productor.
    settings.app_env = "production"
    settings.openbsp_webhook_token = token
    assert _post(client, _payload(sender=None)).status_code == 401


def test_en_produccion_con_las_dos_credenciales_pasa(client, settings):
    settings.app_env = "production"
    settings.openbsp_webhook_token = TOKEN_LARGO
    headers = {"Authorization": f"Bearer {TOKEN_LARGO}"}
    assert _post(client, _payload(sender=None), headers=headers).status_code == 200
    assert _post(client, _payload(sender=None)).status_code == 401  # sin el header


def test_en_desarrollo_el_token_sigue_siendo_opcional(client, settings):
    settings.app_env = "development"
    settings.openbsp_webhook_token = ""
    assert _post(client, _payload(sender=None)).status_code == 200


@pytest.mark.parametrize(
    "env,token,falla",
    [("production", "", True), ("PRODUCTION", "corto", True), ("production", TOKEN_LARGO, False),
     ("development", "", False)],
)
def test_validar_al_arrancar(env, token, falla):
    s = Settings(app_env=env, openbsp_webhook_secret=SECRETO, openbsp_webhook_token=token)
    if falla:
        with pytest.raises(RuntimeError, match="OPENBSP_WEBHOOK_TOKEN"):
            webhooks.validar_al_arrancar(s)
    else:
        webhooks.validar_al_arrancar(s)


def test_validar_al_arrancar_en_produccion_sin_secreto():
    s = Settings(app_env="production", openbsp_webhook_secret="", openbsp_webhook_token=TOKEN_LARGO)
    with pytest.raises(RuntimeError, match="OPENBSP_WEBHOOK_SECRET"):
        webhooks.validar_al_arrancar(s)


# ---------------------------------------------------------------------------
# Con base de datos
# ---------------------------------------------------------------------------


@pytest.mark.db
def test_guarda_y_encola(db_client, cola, org, db_session):
    resp = _post(db_client, _payload(numero=org["numero"]))
    assert resp.json() == {"resultado": "guardado"}
    [msg] = _mensajes(db_session, org["id"])
    assert (msg.from_phone, msg.message_type, msg.text_body) == (
        TELEFONO, "texto", "Hoy abonamos el lote")
    assert msg.producer_id == org["producer_id"] and msg.processing_status == "recibido"
    assert cola.encolados == [(org["id"], msg.id)]


@pytest.mark.db
def test_audio_guarda_la_referencia(db_client, org, db_session):
    content = {"version": "1", "type": "file", "kind": "audio",
               "file": {"mime_type": "audio/ogg",
                        "uri": "internal://media/organizations/o/attachments/" + "h" * 64}}
    _post(db_client, _payload(numero=org["numero"], content=content))
    [msg] = _mensajes(db_session, org["id"])
    assert msg.message_type == "audio" and msg.media_ref.startswith("internal://media/")


@pytest.mark.db
def test_duplicado_no_se_guarda_ni_encola_dos_veces(db_client, cola, org, db_session):
    payload = _payload(numero=org["numero"], external_id="wamid.repetido")
    assert _post(db_client, payload).json() == {"resultado": "guardado"}
    resp = _post(db_client, payload)
    assert resp.status_code == 200 and resp.json() == {"resultado": "duplicado"}
    assert len(_mensajes(db_session, org["id"])) == 1
    assert len(cola.encolados) == 1


@pytest.mark.db
def test_numero_desconocido_no_se_procesa(db_client, cola, org, db_session):
    resp = _post(db_client, _payload(numero=_numero()))
    assert resp.status_code == 200 and resp.json() == {"resultado": "numero_desconocido"}
    assert _mensajes(db_session, org["id"]) == []
    assert db_session.scalars(select(InboundMessage)).all() == []
    assert cola.encolados == []


@pytest.mark.db
def test_remitente_no_registrado_no_se_guarda(db_client, cola, org, db_session):
    resp = _post(db_client, _payload(numero=org["numero"], sender="573209999999"))
    assert resp.json() == {"resultado": "remitente_no_registrado"}
    assert _mensajes(db_session, org["id"]) == [] and cola.encolados == []


@pytest.mark.db
def test_falla_al_encolar_no_rompe_el_webhook(db_client, org, db_session):
    def cola_caida(*_):
        raise ConnectionError("Redis caído")

    app.dependency_overrides[webhooks.get_encolar] = lambda: cola_caida
    resp = _post(db_client, _payload(numero=org["numero"]))
    assert resp.status_code == 200 and resp.json() == {"resultado": "guardado"}
    [msg] = _mensajes(db_session, org["id"])
    assert msg.processing_status == "recibido"


@pytest.mark.db
def test_organizacion_por_numero_receptor(repos, org):
    assert find_organization_id_by_whatsapp_address(repos.session, org["numero"]) == org["id"]
    assert find_organization_id_by_whatsapp_address(repos.session, _numero()) is None
    assert find_organization_id_by_whatsapp_address(repos.session, "") is None


# ---------------------------------------------------------------------------
# Sin consentimiento: pide autorización y no procesa nada hasta el SÍ
# ---------------------------------------------------------------------------


def _recibir_y_atender(db_client, cola, repos, canal, textos, org, **kw):
    resp = _post(db_client, _payload(numero=org["numero"], **kw))
    assert resp.json() == {"resultado": "guardado"}
    org_id, msg_id = cola.encolados[-1]
    return msg_id, atender_mensaje(repos, canal, org_id, msg_id, textos)


@pytest.mark.db
def test_sin_consentimiento_pide_autorizacion_y_no_procesa(
    db_client, cola, repos, canal, textos, org
):
    msg_id, resultado = _recibir_y_atender(db_client, cola, repos, canal, textos, org)

    assert resultado == "consentimiento_solicitado"
    [enviado] = canal.sent
    assert (enviado.channel_address, enviado.to_phone) == (org["numero"], TELEFONO)
    assert "Asocafé Huila" in enviado.text and "SÍ" in enviado.text
    msg = repos.messages.get(org["id"], msg_id)
    assert msg.processing_status == "sin_consentimiento"
    assert msg.text_body is None and msg.media_ref is None  # no se conserva el contenido
    assert repos.producers.get(org["id"], org["producer_id"]).consent_status == "pendiente"


@pytest.mark.db
def test_audio_sin_consentimiento_no_se_descarga(db_client, cola, repos, canal, textos, org):
    content = {"version": "1", "type": "file", "kind": "audio",
               "file": {"mime_type": "audio/ogg", "uri": "fixture://nota.ogg"}}
    canal.download_media = lambda ref: pytest.fail("No debía descargar el audio")
    msg_id, resultado = _recibir_y_atender(db_client, cola, repos, canal, textos, org,
                                           content=content)
    assert resultado == "consentimiento_solicitado"
    assert repos.messages.get(org["id"], msg_id).media_ref is None


@pytest.mark.db
def test_audio_que_dice_si_no_cuenta_como_autorizacion(db_client, cola, repos, canal, textos, org):
    content = {"version": "1", "type": "file", "kind": "audio", "text": "SÍ",
               "file": {"mime_type": "audio/ogg", "uri": "fixture://nota.ogg"}}
    _, resultado = _recibir_y_atender(db_client, cola, repos, canal, textos, org, content=content)
    assert resultado == "consentimiento_solicitado"
    assert repos.producers.get(org["id"], org["producer_id"]).consent_status == "pendiente"


@pytest.mark.db
def test_si_autoriza_y_desde_ahi_se_procesa(db_client, cola, repos, canal, textos, org):
    _recibir_y_atender(db_client, cola, repos, canal, textos, org, texto="Hoy abonamos")
    msg_id, resultado = _recibir_y_atender(db_client, cola, repos, canal, textos, org,
                                           texto="Sí.")
    assert resultado == "consentimiento_aceptado"
    assert canal.sent[-1].text == textos.aceptado
    producer = repos.producers.get(org["id"], org["producer_id"])
    assert producer.consent_status == "aceptado" and producer.consent_at is not None
    si = repos.messages.get(org["id"], msg_id)
    assert si.processing_status == "procesado" and si.text_body == "Sí."  # constancia

    enviados = len(canal.sent)
    msg_id, resultado = _recibir_y_atender(db_client, cola, repos, canal, textos, org,
                                           texto="Fumigamos MIR-01")
    assert resultado == "listo_para_procesar"
    assert len(canal.sent) == enviados  # ya no se pide autorización
    msg = repos.messages.get(org["id"], msg_id)
    assert msg.processing_status == "recibido" and msg.text_body == "Fumigamos MIR-01"


@pytest.mark.db
def test_no_autoriza(db_client, cola, repos, canal, textos, org):
    _, resultado = _recibir_y_atender(db_client, cola, repos, canal, textos, org, texto="NO")
    assert resultado == "consentimiento_rechazado"
    assert canal.sent[-1].text == textos.rechazado
    assert repos.producers.get(org["id"], org["producer_id"]).consent_status == "revocado"

    _, resultado = _recibir_y_atender(db_client, cola, repos, canal, textos, org,
                                      texto="Hoy recogimos")
    assert resultado == "consentimiento_solicitado"


@pytest.mark.db
def test_mensaje_atendido_dos_veces_solo_responde_una(db_client, cola, repos, canal, textos, org):
    msg_id, _ = _recibir_y_atender(db_client, cola, repos, canal, textos, org)
    assert atender_mensaje(repos, canal, org["id"], msg_id, textos) == "omitido"
    assert len(canal.sent) == 1


@pytest.mark.db
def test_atender_mensaje_de_otra_organizacion_no_hace_nada(
    db_client, cola, repos, canal, textos, org
):
    resp = _post(db_client, _payload(numero=org["numero"]))
    assert resp.json() == {"resultado": "guardado"}
    _, msg_id = cola.encolados[-1]
    otra = uuid.uuid4()
    repos.organizations.create(otra, name="Otra", slug=f"o-{otra.hex[:8]}")
    assert atender_mensaje(repos, canal, otra, msg_id, textos) == "omitido"
    assert canal.sent == []
    assert repos.messages.get(org["id"], msg_id).processing_status == "recibido"
