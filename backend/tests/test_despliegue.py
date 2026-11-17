"""Revisa la configuración de producción (docker-compose.prod.yml, Caddyfile, .env.example).

Estos archivos están en la raíz del repositorio, que no se monta en el contenedor
de desarrollo: ahí estas pruebas se saltan. Corren en la CI y con pytest desde
backend/ en una copia del repositorio.
"""

import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE_PROD = RAIZ / "docker-compose.prod.yml"
CADDYFILE = RAIZ / "deploy" / "Caddyfile"
ENV_EJEMPLO = RAIZ / ".env.example"

pytestmark = pytest.mark.skipif(
    not COMPOSE_PROD.exists(), reason="docker-compose.prod.yml no está a la vista (contenedor)"
)

HERRAMIENTAS_INTERNAS = {"adminer", "flower", "dozzle"}


@pytest.fixture(scope="module")
def servicios() -> dict:
    return yaml.safe_load(COMPOSE_PROD.read_text(encoding="utf-8"))["services"]


def _puertos(servicio: dict) -> list[str]:
    return [str(p) for p in servicio.get("ports", [])]


def test_solo_caddy_publica_puertos_hacia_afuera(servicios: dict) -> None:
    publicos = {
        nombre: puertos
        for nombre, s in servicios.items()
        if (puertos := [p for p in _puertos(s) if not p.startswith("127.0.0.1:")])
    }
    assert set(publicos) == {"caddy"}
    assert sorted(publicos["caddy"]) == ["443:443", "443:443/udp", "80:80"]


def test_herramientas_internas_solo_en_localhost(servicios: dict) -> None:
    for nombre in HERRAMIENTAS_INTERNAS:
        puertos = _puertos(servicios[nombre])
        assert puertos, f"{nombre} debe publicarse en 127.0.0.1 para el túnel SSH"
        assert all(p.startswith("127.0.0.1:") for p in puertos), nombre


def test_base_redis_api_y_web_sin_puertos(servicios: dict) -> None:
    for nombre in ("db", "redis", "api", "worker", "beat", "web"):
        assert not _puertos(servicios[nombre]), nombre


def test_produccion_usa_imagenes_de_ghcr_con_tag(servicios: dict) -> None:
    for nombre in ("api", "worker", "beat", "web"):
        servicio = servicios[nombre]
        assert "build" not in servicio, f"{nombre} no debe compilarse en el servidor"
        assert servicio["image"].startswith("${REGISTRO:-ghcr.io}/${GHCR_OWNER"), nombre
        assert "${IMAGE_TAG" in servicio["image"], nombre


def test_codigo_no_se_monta_desde_el_host(servicios: dict) -> None:
    for nombre in ("api", "worker", "beat", "web"):
        for volumen in servicios[nombre].get("volumes", []):
            assert not str(volumen).startswith(("./backend", "./web")), nombre


def test_un_solo_beat(servicios: dict) -> None:
    assert "deploy" not in servicios["beat"] or servicios["beat"]["deploy"].get("replicas", 1) == 1


def test_sin_secretos_literales(servicios: dict) -> None:
    sospechosos = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY)", re.IGNORECASE)
    for nombre, servicio in servicios.items():
        entorno = servicio.get("environment") or {}
        for clave, valor in entorno.items():
            if sospechosos.search(clave):
                assert str(valor).startswith("${"), f"{nombre}.{clave} debe venir del .env"


def test_caddy_apunta_a_api_y_web() -> None:
    texto = CADDYFILE.read_text(encoding="utf-8")
    assert "{$API_DOMAIN}" in texto and "reverse_proxy api:8000" in texto
    assert "{$WEB_DOMAIN}" in texto and "reverse_proxy web:3000" in texto
    # Sin log de accesos: la URL del webhook lleva el secreto.
    assert not re.search(r"^\s*log\b", texto, re.MULTILINE)


def test_env_ejemplo_trae_las_variables_de_produccion() -> None:
    claves = {
        linea.split("=", 1)[0].lstrip("# ").strip()
        for linea in ENV_EJEMPLO.read_text(encoding="utf-8").splitlines()
        if "=" in linea
    }
    for clave in (
        "COMPOSE_FILE",
        "GHCR_OWNER",
        "IMAGE_TAG",
        "API_DOMAIN",
        "WEB_DOMAIN",
        "ACME_EMAIL",
        "RESPALDO_REMOTO",
    ):
        assert clave in claves, clave
