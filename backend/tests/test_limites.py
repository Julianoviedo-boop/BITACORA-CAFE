"""Límites de uso por productor y por organización (app/domain/limites.py)."""

import datetime as dt
import random
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.channels import ChannelTemporaryError
from app.channels.fake import FakeChannel
from app.db.repositories import Repositories
from app.domain import limites
from app.domain.limites import ContadorRedis, ReglasLimites, Tope, cargar_reglas, revisar_cupo

_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]
AHORA = dt.datetime(2026, 11, 16, 15, 30, tzinfo=dt.UTC)  # 10:30 a. m. en Colombia


class ContadorMemoria:
    """Igual que ContadorRedis pero en memoria (conjuntos y marcas)."""

    def __init__(self) -> None:
        self.conjuntos: dict[str, set[str]] = {}
        self.marcas: set[str] = set()

    def contar(self, llave: str, miembro: str, vida_s: int) -> int:
        self.conjuntos.setdefault(llave, set()).add(miembro)
        return len(self.conjuntos[llave])

    def marcar_una_vez(self, llave: str, vida_s: int) -> bool:
        if llave in self.marcas:
            return False
        self.marcas.add(llave)
        return True

    def desmarcar(self, llave: str) -> None:
        self.marcas.discard(llave)


class ContadorCaido:
    def contar(self, *a):
        raise ConnectionError("Redis no responde")

    def marcar_una_vez(self, *a):
        raise ConnectionError("Redis no responde")

    def desmarcar(self, *a):
        raise ConnectionError("Redis no responde")


def _reglas(productor=(2, 5), organizacion=(4, 10), max_caracteres=50) -> ReglasLimites:
    return ReglasLimites(
        por_productor=Tope(*productor), por_organizacion=Tope(*organizacion),
        max_caracteres_texto=max_caracteres, max_tokens_respuesta=8000,
        avisos={"limite_hora": "Espere un ratico", "limite_dia": "Mañana seguimos",
                "texto_muy_largo": "Máximo {max_caracteres} letras"},
    )


# ---------------------------------------------------------------------------
# config/limites.yaml
# ---------------------------------------------------------------------------


def test_config_del_repositorio_es_valida():
    config_dir = next((p for p in _CONFIG if (p / "limites.yaml").is_file()), None)
    if config_dir is None:
        pytest.skip("No se encontró config/limites.yaml")
    r = cargar_reglas(config_dir)
    assert r.por_productor.por_hora <= r.por_productor.por_dia
    assert r.por_productor.por_dia <= r.por_organizacion.por_dia
    assert r.max_caracteres_texto >= 4000  # lo que deja escribir WhatsApp no se corta
    assert "{max_caracteres}" not in r.aviso("texto_muy_largo")


@pytest.mark.parametrize("contenido,error", [
    ("por_organizacion: {por_hora: 1, por_dia: 1}\n", "por_productor"),
    ("por_productor: {por_hora: 0, por_dia: 1}\n", "por_hora"),
    ("por_productor: {por_hora: 5, por_dia: 1}\n", "menor"),
    ("por_productor: {por_hora: sí, por_dia: 1}\n", "por_hora"),
])
def test_config_invalida(tmp_path, contenido, error):
    avisos = "avisos: {limite_hora: a, limite_dia: b, texto_muy_largo: c}\n"
    resto = ("por_organizacion: {por_hora: 1, por_dia: 1}\nmax_caracteres_texto: 10\n"
             "max_tokens_respuesta: 10\n")
    if "por_organizacion" in contenido:
        resto = "max_caracteres_texto: 10\nmax_tokens_respuesta: 10\n"
    (tmp_path / "limites.yaml").write_text(contenido + resto + avisos, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        cargar_reglas(tmp_path)


def test_config_sin_avisos(tmp_path):
    (tmp_path / "limites.yaml").write_text("max_caracteres_texto: 10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="avisos"):
        cargar_reglas(tmp_path)


def test_aviso_lleva_el_maximo_con_punto_de_miles():
    assert _reglas(max_caracteres=4000).aviso("texto_muy_largo") == "Máximo 4.000 letras"


# ---------------------------------------------------------------------------
# Ventanas (hora y día de Colombia)
# ---------------------------------------------------------------------------


def test_ventanas_en_hora_de_colombia():
    # 04:30 UTC del 16 = 11:30 p. m. del 15 en Colombia.
    hora, dia = limites.ventanas(dt.datetime(2026, 11, 16, 4, 30, tzinfo=dt.UTC))
    assert (hora.id, dia.id) == ("2026111523", "20261115")
    assert hora.vida_s > 3600 and dia.vida_s > 86400


def test_ventanas_exigen_zona_horaria():
    with pytest.raises(ValueError):
        limites.ventanas(dt.datetime(2026, 11, 16, 4, 30))


# ---------------------------------------------------------------------------
# revisar_cupo (sin base de datos)
# ---------------------------------------------------------------------------


def _ids():
    return uuid.uuid4(), uuid.uuid4()


def test_dentro_del_tope_pasa_y_al_pasarse_se_frena():
    c, (org, prod) = ContadorMemoria(), _ids()
    r = _reglas(productor=(2, 5))
    assert revisar_cupo(c, r, org, prod, uuid.uuid4(), AHORA) is None
    assert revisar_cupo(c, r, org, prod, uuid.uuid4(), AHORA) is None
    excedido = revisar_cupo(c, r, org, prod, uuid.uuid4(), AHORA)
    assert excedido is not None
    assert (excedido.alcance, excedido.ventana.nombre, excedido.tope) == ("productor", "hora", 2)


def test_el_mismo_mensaje_no_cuenta_dos_veces():
    # Si la tarea se reintenta, el mensaje no se gasta el cupo otra vez.
    c, (org, prod), msg = ContadorMemoria(), _ids(), uuid.uuid4()
    r = _reglas(productor=(1, 5))
    for _ in range(5):
        assert revisar_cupo(c, r, org, prod, msg, AHORA) is None


def test_la_hora_siguiente_vuelve_a_tener_cupo():
    c, (org, prod) = ContadorMemoria(), _ids()
    r = _reglas(productor=(1, 5))
    revisar_cupo(c, r, org, prod, uuid.uuid4(), AHORA)
    assert revisar_cupo(c, r, org, prod, uuid.uuid4(), AHORA) is not None
    una_hora_despues = AHORA + dt.timedelta(hours=1)
    assert revisar_cupo(c, r, org, prod, uuid.uuid4(), una_hora_despues) is None


def test_tope_del_dia_se_avisa_antes_que_el_de_la_hora():
    c, (org, prod) = ContadorMemoria(), _ids()
    r = _reglas(productor=(2, 2))
    for _ in range(2):
        revisar_cupo(c, r, org, prod, uuid.uuid4(), AHORA)
    excedido = revisar_cupo(c, r, org, prod, uuid.uuid4(), AHORA)
    assert excedido.ventana.nombre == "dia"


def test_productor_que_se_pasa_no_gasta_el_cupo_de_la_organizacion():
    c, (org, abusivo), companero = ContadorMemoria(), _ids(), uuid.uuid4()
    r = _reglas(productor=(2, 100), organizacion=(3, 100))
    for _ in range(20):
        revisar_cupo(c, r, org, abusivo, uuid.uuid4(), AHORA)
    # La organización solo contó 2 (los que el abusivo tenía permitidos).
    assert revisar_cupo(c, r, org, companero, uuid.uuid4(), AHORA) is None


def test_tope_de_la_organizacion():
    c, org = ContadorMemoria(), uuid.uuid4()
    r = _reglas(productor=(10, 100), organizacion=(3, 100))
    productores = [uuid.uuid4() for _ in range(4)]
    resultados = [revisar_cupo(c, r, org, p, uuid.uuid4(), AHORA) for p in productores]
    assert resultados[:3] == [None, None, None]
    assert resultados[3].alcance == "organizacion"


def test_organizaciones_no_comparten_cupo():
    c, prod = ContadorMemoria(), uuid.uuid4()
    r = _reglas(productor=(1, 100))
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    revisar_cupo(c, r, org_a, prod, uuid.uuid4(), AHORA)
    assert revisar_cupo(c, r, org_b, prod, uuid.uuid4(), AHORA) is None


# ---------------------------------------------------------------------------
# atender (con base de datos)
# ---------------------------------------------------------------------------

TELEFONO = "+573101234599"


@pytest.fixture
def repos(db_session: Session) -> Repositories:
    return Repositories(db_session)


@pytest.fixture
def canal(tmp_path) -> FakeChannel:
    return FakeChannel(tmp_path)


@pytest.fixture
def org(repos):
    org_id = uuid.uuid4()
    numero = str(random.randint(10**14, 10**15 - 1))
    repos.organizations.create(org_id, name="Asocafé Prueba", slug=f"l-{org_id.hex[:8]}",
                               whatsapp_address=numero)
    producer = repos.producers.add(org_id, full_name="Rosa Elena Muñoz", phone=TELEFONO)
    repos.session.flush()
    return {"id": org_id, "numero": numero, "producer_id": producer.id}


def _mensaje(repos, org, texto="Hoy abonamos el lote", tipo="texto"):
    msg, _ = repos.messages.add_if_new(
        org["id"], f"wamid.{uuid.uuid4().hex}", producer_id=org["producer_id"],
        from_phone=TELEFONO, message_type=tipo, text_body=texto,
    )
    return msg


def _atender(repos, canal, org, msg, contador, reglas=None):
    return limites.atender(repos, canal, contador, reglas or _reglas(), org["id"], msg.id, AHORA)


def test_dentro_del_cupo_sigue_sin_cambios(repos, canal, org):
    msg = _mensaje(repos, org)
    assert _atender(repos, canal, org, msg, ContadorMemoria()) is None
    assert repos.messages.get(org["id"], msg.id).processing_status == "recibido"
    assert canal.sent == []


def test_al_pasarse_no_se_procesa_y_se_avisa_una_sola_vez(repos, canal, org):
    contador = ContadorMemoria()
    reglas = _reglas(productor=(2, 100))
    msgs = [_mensaje(repos, org) for _ in range(5)]
    resultados = [_atender(repos, canal, org, m, contador, reglas) for m in msgs]
    assert resultados == [None, None, "limite_excedido", "limite_excedido", "limite_excedido"]
    for m in msgs[2:]:
        guardado = repos.messages.get(org["id"], m.id)
        assert guardado.processing_status == "error"
        assert guardado.error_detail.startswith("limite_productor_hora")
    # Un solo aviso, no uno por cada mensaje de más.
    assert [(s.to_phone, s.text) for s in canal.sent] == [(TELEFONO, "Espere un ratico")]


def test_texto_muy_largo_no_va_a_claude(repos, canal, org):
    msg = _mensaje(repos, org, texto="a" * 51)
    assert _atender(repos, canal, org, msg, ContadorMemoria()) == "texto_muy_largo"
    guardado = repos.messages.get(org["id"], msg.id)
    assert guardado.processing_status == "error"
    assert guardado.error_detail == "texto_muy_largo: 51 caracteres"
    assert [s.text for s in canal.sent] == ["Máximo 50 letras"]


def test_texto_justo_en_el_maximo_pasa(repos, canal, org):
    msg = _mensaje(repos, org, texto="a" * 50)
    assert _atender(repos, canal, org, msg, ContadorMemoria()) is None


def test_audio_no_se_mide_por_caracteres(repos, canal, org):
    # Los audios se limitan por duración (config/transcripcion.yaml).
    msg = _mensaje(repos, org, texto=None, tipo="audio")
    assert _atender(repos, canal, org, msg, ContadorMemoria()) is None


def test_sin_redis_deja_pasar(repos, canal, org, caplog):
    msg = _mensaje(repos, org)
    assert _atender(repos, canal, org, msg, ContadorCaido()) is None
    assert "no se pudo revisar el cupo" in caplog.text


def test_mensaje_ya_atendido_no_se_toca(repos, canal, org):
    msg = _mensaje(repos, org)
    repos.messages.set_processing_status(org["id"], msg.id, "procesado", expected="recibido")
    assert _atender(repos, canal, org, msg, ContadorMemoria(), _reglas(productor=(1, 1))) is None


def test_si_el_aviso_falla_se_puede_reintentar(repos, canal, org, monkeypatch):
    contador = ContadorMemoria()
    reglas = _reglas(productor=(1, 100))
    assert _atender(repos, canal, org, _mensaje(repos, org), contador, reglas) is None
    msg = _mensaje(repos, org)

    def caido(*a):
        raise ChannelTemporaryError("OpenBSP caído")

    monkeypatch.setattr(canal, "send_text", caido)
    with pytest.raises(ChannelTemporaryError):
        _atender(repos, canal, org, msg, contador, reglas)
    # La marca del aviso se quitó: en el reintento sí se le avisa.
    assert contador.marcas == set()


# ---------------------------------------------------------------------------
# ContadorRedis contra el Redis de verdad (se salta si no hay Redis)
# ---------------------------------------------------------------------------


@pytest.fixture
def contador_redis():
    import redis

    from app.config import get_settings

    cliente = redis.Redis.from_url(get_settings().redis_url, socket_timeout=1,
                                   socket_connect_timeout=1)
    try:
        cliente.ping()
    except redis.RedisError as exc:
        pytest.skip(f"Redis no disponible: {exc}")
    prefijo = f"prueba:{uuid.uuid4().hex}"
    yield ContadorRedis(cliente), prefijo
    for llave in cliente.scan_iter(f"{prefijo}*"):
        cliente.delete(llave)


def test_contador_redis_cuenta_sin_repetir_y_vence(contador_redis):
    contador, prefijo = contador_redis
    llave = f"{prefijo}:conjunto"
    assert contador.contar(llave, "a", 60) == 1
    assert contador.contar(llave, "a", 60) == 1
    assert contador.contar(llave, "b", 60) == 2
    assert 0 < contador._r.ttl(llave) <= 60


def test_contador_redis_marca_una_vez(contador_redis):
    contador, prefijo = contador_redis
    llave = f"{prefijo}:aviso"
    assert contador.marcar_una_vez(llave, 60) is True
    assert contador.marcar_una_vez(llave, 60) is False
    contador.desmarcar(llave)
    assert contador.marcar_una_vez(llave, 60) is True
