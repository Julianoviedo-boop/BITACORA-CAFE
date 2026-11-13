"""Envío de alertas: ventana de 24 horas, plantilla o solo panel."""

import datetime as dt
import uuid
from pathlib import Path

import pytest

from app.channels.base import ChannelError, ChannelTemporaryError
from app.channels.fake import FakeChannel, SentTemplate, SentText
from app.db.repositories import Repositories
from app.domain import alertas

pytestmark = pytest.mark.db

AHORA = dt.datetime(2026, 11, 13, 15, 0, tzinfo=dt.UTC)
NUMERO = "100000000000077"
SIN_PLANTILLA = alertas.ReglasAlertas()
CON_PLANTILLA = alertas.ReglasAlertas(plantilla=alertas.Plantilla(nombre="alerta_lote",
                                                                  idioma="es"))


@pytest.fixture
def repos(db_session) -> Repositories:
    return Repositories(db_session)


@pytest.fixture
def canal(tmp_path: Path) -> FakeChannel:
    return FakeChannel(tmp_path)


@pytest.fixture
def mundo(repos):
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name="Asocafé", slug=f"t-{org_id.hex[:8]}",
                               whatsapp_address=NUMERO)
    finca = repos.farms.add(org_id, name="El Recreo")
    lote = repos.lots.add(org_id, farm_id=finca.id, code="ESP-01", name="La Esperanza")
    alerta = repos.alerts.open_if_absent(org_id, lot_id=lote.id, rule_code="valor:secado:humedad",
                                         message="Humedad del lote La Esperanza en 14%.")
    return org_id, finca, alerta


def productor(repos, org_id, finca, telefono, *, ultimo_mensaje_hace=None, consentimiento="aceptado"):
    p = repos.producers.add(org_id, full_name="Productor", phone=telefono, farm_id=finca.id)
    repos.producers.record_consent(org_id, p.id, consentimiento)
    if ultimo_mensaje_hace is not None:
        conv = repos.conversations.get_or_create(org_id, p.id)
        repos.conversations.update(org_id, conv.id, last_inbound_at=AHORA - ultimo_mensaje_hace)
    return p


def _enviar(repos, canal, reglas, mundo):
    org_id, _, alerta = mundo
    return alertas.enviar(repos, canal, reglas, org_id, alerta.id, AHORA)


def test_dentro_de_la_ventana_manda_texto(repos, canal, mundo):
    org_id, finca, alerta = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=3))

    assert _enviar(repos, canal, CON_PLANTILLA, mundo) == "enviada"
    assert canal.sent == [SentText(NUMERO, "+573001110001", "Humedad del lote La Esperanza en 14%.")]
    assert canal.templates == []
    alerta = repos.alerts.get(org_id, alerta.id)
    assert alerta.status == "enviada" and alerta.sent_at is not None


def test_fuera_de_la_ventana_sin_plantilla_queda_solo_en_el_panel(repos, canal, mundo):
    org_id, finca, alerta = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=30))

    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "solo_panel"
    assert canal.sent == [] and canal.templates == []
    assert repos.alerts.get(org_id, alerta.id).status == "pendiente"
    assert [a.id for a in repos.alerts.list_open(org_id)] == [alerta.id]


def test_productor_que_nunca_escribio_esta_fuera_de_la_ventana(repos, canal, mundo):
    org_id, finca, _ = mundo
    productor(repos, org_id, finca, "+573001110001")
    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "solo_panel"


def test_fuera_de_la_ventana_con_plantilla(repos, canal, mundo):
    org_id, finca, alerta = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(days=5))

    assert _enviar(repos, canal, CON_PLANTILLA, mundo) == "enviada"
    assert canal.sent == []
    assert canal.templates == [SentTemplate(NUMERO, "+573001110001", "alerta_lote", "es",
                                            ("Humedad del lote La Esperanza en 14%.",))]
    assert repos.alerts.get(org_id, alerta.id).status == "enviada"


def test_varios_productores_cada_uno_segun_su_ventana(repos, canal, mundo):
    org_id, finca, _ = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=1))
    productor(repos, org_id, finca, "+573001110002", ultimo_mensaje_hace=dt.timedelta(hours=40))
    # Sin autorización de datos: no se le escribe.
    productor(repos, org_id, finca, "+573001110003", ultimo_mensaje_hace=dt.timedelta(hours=1),
              consentimiento="pendiente")
    # De otra finca: no es su lote.
    otra = repos.farms.add(org_id, name="Otra finca")
    productor(repos, org_id, otra, "+573001110004", ultimo_mensaje_hace=dt.timedelta(hours=1))

    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "enviada"
    assert [s.to_phone for s in canal.sent] == ["+573001110001"]
    assert canal.templates == []


def test_no_se_manda_dos_veces(repos, canal, mundo):
    org_id, finca, _ = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=1))
    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "enviada"
    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "omitida"
    assert len(canal.sent) == 1


def test_alerta_resuelta_antes_de_enviar_no_se_manda(repos, canal, mundo):
    org_id, finca, alerta = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=1))
    repos.alerts.resolve_open(org_id, alerta.lot_id, alerta.rule_code)
    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "omitida"
    assert canal.sent == []


def test_alerta_de_otra_organizacion_no_se_manda(repos, canal, mundo):
    org_id, finca, alerta = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=1))
    assert alertas.enviar(repos, canal, SIN_PLANTILLA, uuid.uuid4(), alerta.id, AHORA) == "omitida"
    assert canal.sent == []


class CanalQueFalla(FakeChannel):
    def __init__(self, tmp_path, malos: dict[str, Exception]) -> None:
        super().__init__(tmp_path)
        self.malos = malos

    def send_text(self, channel_address, to_phone, text):
        if to_phone in self.malos:
            raise self.malos[to_phone]
        super().send_text(channel_address, to_phone, text)


def test_un_numero_rechazado_no_impide_avisar_a_los_demas(repos, tmp_path, mundo):
    org_id, finca, _ = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=1))
    productor(repos, org_id, finca, "+573001110002", ultimo_mensaje_hace=dt.timedelta(hours=1))
    canal = CanalQueFalla(tmp_path, {"+573001110001": ChannelError("número bloqueado")})
    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "enviada"
    assert [s.to_phone for s in canal.sent] == ["+573001110002"]


def test_falla_temporal_se_propaga_para_reintentar(repos, tmp_path, mundo):
    org_id, finca, alerta = mundo
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=1))
    canal = CanalQueFalla(tmp_path, {"+573001110001": ChannelTemporaryError("503")})
    with pytest.raises(ChannelTemporaryError):
        _enviar(repos, canal, SIN_PLANTILLA, mundo)
    assert repos.alerts.get(org_id, alerta.id).status == "pendiente"


def test_organizacion_sin_numero_de_whatsapp(repos, canal, mundo):
    org_id, finca, _ = mundo
    repos.organizations.update(org_id, whatsapp_address=None)
    productor(repos, org_id, finca, "+573001110001", ultimo_mensaje_hace=dt.timedelta(hours=1))
    assert _enviar(repos, canal, SIN_PLANTILLA, mundo) == "solo_panel"
    assert canal.sent == []
