"""Reporte de trazabilidad en PDF (app/reports/, D-023) y GET /lotes/{id}/reporte.pdf."""

import datetime as dt
import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import panel
from app.channels.fake import FakeChannel
from app.config import Settings
from app.db.models import LotEvent
from app.db.repositories import Repositories, unit_of_work
from app.domain import historia_lote
from app.domain.conversacion import cargar_textos
from app.extraction.prompts import ZONA_COLOMBIA
from app.extraction.schema import cargar_catalogo
from app.main import app
from app.reports import datos as datos_mod
from app.reports import whatsapp
from app.reports.datos import datos_reporte
from app.reports.pdf import html_reporte, nombre_archivo, qr_svg

CONFIG = Path(__file__).resolve().parents[2] / "config"
TOKEN = "t" * 48
BASE = "https://panel.ejemplo.co"
HAY_WEASYPRINT = importlib.util.find_spec("weasyprint") is not None
requiere_weasyprint = pytest.mark.skipif(not HAY_WEASYPRINT, reason="WeasyPrint no está instalado")


@pytest.fixture(scope="module")
def catalogo():
    return cargar_catalogo(CONFIG)


@pytest.fixture(scope="module")
def textos():
    return cargar_textos(CONFIG)


def t(dia: int, hora: int = 9) -> dt.datetime:
    return dt.datetime(2026, 11, dia, hora, tzinfo=ZONA_COLOMBIA)


# --- sin base de datos ---


def test_dato_obligatorio_que_falta_sale_como_no_registrado(catalogo, textos):
    ev = LotEvent(id=uuid.uuid4(), event_type="abonada", occurred_at=t(3), status="confirmado",
                  data={"insumo": {"valor": "urea", "unidad": None}})
    publicos = historia_lote._datos(ev, catalogo, textos, solo_publicos=True)
    assert [(d.etiqueta, d.valor) for d in publicos] == [
        ("Abono", "urea"), ("Cantidad", "No registrado")]
    # En el panel no se inventa nada: solo lo que hay.
    assert [d.etiqueta for d in historia_lote._datos(ev, catalogo, textos)] == ["Abono"]


def test_qr_es_un_svg_embebido():
    assert qr_svg(f"{BASE}/l/abc").startswith("data:image/svg+xml")


def test_url_publica_sin_doble_barra():
    lote = type("L", (), {"public_token": "tok"})()
    assert datos_mod.url_publica(BASE + "/", lote) == f"{BASE}/l/tok"


# --- con base de datos ---


@pytest.fixture
def repos(db_session: Session) -> Repositories:
    return Repositories(db_session)


def _confirmado(repos, org, lot_id, tipo, momento, data, texto=None):
    ev = repos.events.add(org, lot_id=lot_id, event_type=tipo, occurred_at=momento, data=data,
                          original_text=texto)
    repos.events.confirm(org, ev.id)
    return ev


@pytest.fixture
def mundo(repos, db_session):
    org = uuid.uuid4()
    otra = uuid.uuid4()
    numero = str(uuid.uuid4().int)[:15]
    repos.organizations.create(org, name="Asociación del Sur", slug=f"r-{org.hex[:8]}",
                               whatsapp_address=numero)
    repos.organizations.create(otra, name="Otra", slug=f"r-{otra.hex[:8]}")
    finca = repos.farms.add(org, name="El Mirador", municipality="Pitalito", department="Huila",
                            altitude_m=1650)
    productor = repos.producers.add(org, farm_id=finca.id, full_name="Rosa Elena Muñoz",
                                    phone="+573109998877")
    repos.producers.record_consent(org, productor.id, "aceptado")
    lote = repos.lots.add(org, farm_id=finca.id, name="La Esperanza", code="MIR-01",
                          variety="Castillo")  # sin proceso: debe decir "No registrado"
    vacio = repos.lots.add(org, farm_id=finca.id, name="La Loma", code="BUE-01")
    _confirmado(repos, org, lote.id, "abonada", t(3, 7),
                {"insumo": {"valor": "17-6-18", "unidad": None},
                 "cantidad": {"valor": 4, "unidad": "bulto"},
                 "jornales": {"valor": 2, "unidad": "jornal"}},
                texto="lo hicimos con don Pedro")
    _confirmado(repos, org, lote.id, "volteo", t(22, 14),
                {"temperatura": {"valor": 24, "unidad": "grado_celsius"}})
    _confirmado(repos, org, lote.id, "medicion_fermentacion", t(23, 9),
                {"temperatura": {"valor": 41, "unidad": "grado_celsius"}})
    _confirmado(repos, org, lote.id, "venta", t(23, 15),
                {"cantidad": {"valor": 2, "unidad": "carga"},
                 "precio": {"valor": 2_000_000, "unidad": "peso_por_carga"}})
    repos.events.add(org, lot_id=lote.id, event_type="desyerba", occurred_at=t(24),
                     data={"metodo": {"valor": "machete", "unidad": None}})  # pendiente
    finca_otra = repos.farms.add(otra, name="Ajena")
    lote_otro = repos.lots.add(otra, farm_id=finca_otra.id, name="Ajeno", code="AJ-01")
    msg = repos.messages.add(org, external_id=f"wamid.{uuid.uuid4().hex}",
                             producer_id=productor.id, from_phone=productor.phone,
                             message_type="texto", text_body="reporte lote 1",
                             received_at=t(24, 8))
    db_session.flush()
    return type("Mundo", (), dict(org=org, otra=otra, lote=lote, vacio=vacio,
                                  lote_otro=lote_otro, msg=msg, numero=numero,
                                  telefono=productor.phone))


def _datos(repos, mundo, catalogo, textos, lote=None, **kw):
    return datos_reporte(repos, mundo.org, (lote or mundo.lote).id, base_url=BASE,
                         ahora=t(24, 10), catalogo=catalogo, textos=textos, **kw)


@pytest.mark.db
def test_datos_del_reporte(repos, mundo, catalogo, textos):
    d = _datos(repos, mundo, catalogo, textos)

    assert d.organizacion == "Asociación del Sur"
    assert {f.etiqueta: f.valor for f in d.ficha} == {
        "Finca": "El Mirador", "Ubicación": "Pitalito, Huila", "Altitud": "1.650 m s. n. m.",
        "Lote": "La Esperanza (MIR-01)", "Variedad": "Castillo", "Proceso": "No registrado"}
    # Solo confirmados y públicos: ni la venta ni la desyerba pendiente.
    assert [e.tipo for e in d.eventos] == ["abonada", "volteo", "medicion_fermentacion"]
    assert d.periodo == "Del martes 3 de noviembre de 2026 al lunes 23 de noviembre de 2026"
    assert d.generado == "martes 24 de noviembre de 2026"
    assert d.url_publica == f"{BASE}/l/{mundo.lote.public_token}"
    medidas = {m.dato: m for m in d.mediciones}
    temp = medidas["Temperatura"]
    assert (temp.mediciones, temp.minimo, temp.maximo, temp.ultima) == (
        "2", "24 °C", "41 °C", "41 °C (23 nov)")
    # Lo que no se midió no se estima.
    assert medidas["Humedad"].ultima == "No registrado"


@pytest.mark.db
def test_reporte_de_lote_sin_registros(repos, mundo, catalogo, textos):
    d = _datos(repos, mundo, catalogo, textos, lote=mundo.vacio)
    assert d.eventos == []
    assert d.periodo == "No registrado"
    assert {f.etiqueta: f.valor for f in d.ficha}["Variedad"] == "No registrado"
    assert all(m.ultima == "No registrado" for m in d.mediciones)


@pytest.mark.db
def test_reporte_por_periodo(repos, mundo, catalogo, textos):
    d = _datos(repos, mundo, catalogo, textos, desde=dt.date(2026, 11, 20),
               hasta=dt.date(2026, 11, 22))
    assert [e.tipo for e in d.eventos] == ["volteo"]
    assert d.periodo.startswith("Del viernes 20 de noviembre")


@pytest.mark.db
def test_lote_de_otra_organizacion_no_tiene_reporte(repos, mundo, catalogo, textos):
    assert datos_reporte(repos, mundo.org, mundo.lote_otro.id, base_url=BASE,
                         catalogo=catalogo, textos=textos) is None


@pytest.mark.db
def test_html_sin_datos_personales(repos, mundo, catalogo, textos):
    html = html_reporte(_datos(repos, mundo, catalogo, textos))
    assert "La Esperanza" in html and "No registrado" in html
    assert "data:image/svg+xml" in html
    for privado in ("don Pedro", "Rosa Elena", "+57310", "2.000.000", "jornal", "machete"):
        assert privado not in html


@pytest.mark.db
def test_nombre_del_archivo(repos, mundo, catalogo, textos):
    assert nombre_archivo(_datos(repos, mundo, catalogo, textos)) == "reporte-MIR-01.pdf"


@requiere_weasyprint
@pytest.mark.db
def test_pdf_de_una_a_dos_paginas_con_las_fuentes_incluidas(repos, mundo, catalogo, textos):
    import re

    from weasyprint import HTML

    from app.reports.pdf import CARPETA, generar_pdf

    d = _datos(repos, mundo, catalogo, textos)
    assert generar_pdf(d).startswith(b"%PDF")
    documento = HTML(string=html_reporte(d), base_url=str(CARPETA))
    assert 1 <= len(documento.render().pages) <= 2
    # Las fuentes van incrustadas y son solo las de app/reports/fuentes/: ninguna
    # sale del sistema, así el PDF se ve igual en todos lados.
    plano = documento.write_pdf(uncompressed_pdf=True)
    fuentes = set(re.findall(rb"/BaseFont\s*/[A-Z]{6}\+([A-Za-z0-9\-]+)", plano))
    assert fuentes and all(f.startswith((b"Fraunces", b"Source-Sans-3")) for f in fuentes), fuentes
    assert plano.count(b"/FontFile2") == len(fuentes)


# --- envío por WhatsApp ---


@requiere_weasyprint
@pytest.mark.db
def test_enviar_reporte_por_whatsapp(repos, mundo, textos):
    canal = FakeChannel(Path("."))
    resultado = whatsapp.enviar_reporte(repos, canal, textos, mundo.org, mundo.lote.id,
                                        mundo.msg.id, base_url=BASE, ahora=t(24, 10))
    assert resultado == "enviado"
    [doc] = canal.documents
    assert (doc.channel_address, doc.to_phone) == (mundo.numero, mundo.telefono)
    assert (doc.filename, doc.mime_type) == ("reporte-MIR-01.pdf", "application/pdf")
    assert doc.content.startswith(b"%PDF")
    assert doc.caption == "Reporte de trazabilidad de La Esperanza (MIR-01)."


@pytest.mark.db
def test_reporte_fuera_de_la_ventana_no_se_envia(repos, mundo, textos):
    canal = FakeChannel(Path("."))
    resultado = whatsapp.enviar_reporte(repos, canal, textos, mundo.org, mundo.lote.id,
                                        mundo.msg.id, base_url=BASE, ahora=t(26, 10))
    assert resultado == "fuera_de_ventana"
    assert canal.documents == []


@pytest.mark.db
def test_reporte_de_otra_organizacion_no_se_envia(repos, mundo, textos):
    canal = FakeChannel(Path("."))
    assert whatsapp.enviar_reporte(repos, canal, textos, mundo.otra, mundo.lote.id,
                                   mundo.msg.id, base_url=BASE, ahora=t(24, 10)) == "omitido"
    assert canal.documents == []


@pytest.mark.db
def test_avisar_falla(repos, mundo, textos):
    canal = FakeChannel(Path("."))
    assert whatsapp.avisar_falla(repos, canal, textos, mundo.org, mundo.msg.id, ahora=t(24, 10))
    assert canal.sent[0].text == textos.t("reporte_error")


# --- endpoint ---


@pytest.fixture
def client(monkeypatch, mundo, db_session):
    s = Settings(panel_api_token=TOKEN, panel_organization_id=str(mundo.org),
                 public_web_url=BASE)
    monkeypatch.setattr(panel, "get_settings", lambda: s)
    from app.api import reportes
    monkeypatch.setattr(reportes, "get_settings", lambda: s)
    monkeypatch.setattr(datos_mod, "catalogo_por_defecto", lambda: cargar_catalogo(CONFIG))
    monkeypatch.setattr(datos_mod, "get_textos", lambda: cargar_textos(CONFIG))
    monkeypatch.setattr(historia_lote, "catalogo_por_defecto", lambda: cargar_catalogo(CONFIG))
    monkeypatch.setattr(historia_lote, "get_textos", lambda: cargar_textos(CONFIG))
    app.dependency_overrides[panel.get_unit_of_work] = lambda: lambda: unit_of_work(db_session)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.db
def test_endpoint_exige_el_token(client, mundo):
    assert client.get(f"/lotes/{mundo.lote.id}/reporte.pdf").status_code == 401


@pytest.mark.db
def test_endpoint_no_da_lotes_de_otra_organizacion(client, mundo):
    resp = client.get(f"/lotes/{mundo.lote_otro.id}/reporte.pdf", headers=_auth())
    assert resp.status_code == 404


@pytest.mark.db
def test_endpoint_rechaza_periodo_al_reves(client, mundo):
    resp = client.get(f"/lotes/{mundo.lote.id}/reporte.pdf?desde=2026-11-20&hasta=2026-11-01",
                      headers=_auth())
    assert resp.status_code == 422


@requiere_weasyprint
@pytest.mark.db
def test_endpoint_devuelve_el_pdf(client, mundo):
    resp = client.get(f"/lotes/{mundo.lote.id}/reporte.pdf", headers=_auth())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert 'filename="reporte-MIR-01.pdf"' in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "no-store"
    assert resp.content.startswith(b"%PDF")
