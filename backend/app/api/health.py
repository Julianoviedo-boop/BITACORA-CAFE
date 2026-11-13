"""Endpoint de salud: comprueba base de datos, migraciones y Redis."""

import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, text

from app.config import get_settings
from app.db.session import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["salud"])

# backend/ (o /app en el contenedor), donde están alembic.ini y alembic/.
BACKEND_DIR = Path(__file__).resolve().parents[2]


@lru_cache
def alembic_heads() -> frozenset[str]:
    """Últimas migraciones que trae el código (se leen una vez por proceso)."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


def verificar_migraciones(conn: Connection) -> None:
    """Falla si la base no está en la misma migración que espera el código."""
    # Tabla técnica de Alembic: no es de negocio, no lleva organization_id.
    actuales = frozenset(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    esperadas = alembic_heads()
    if actuales != esperadas:
        raise RuntimeError(
            f"la base está en {sorted(actuales)} y el código espera {sorted(esperadas)}; "
            "corra: alembic upgrade head"
        )


@lru_cache
def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2
    )


def check_db() -> None:
    # Consulta técnica de conectividad: no toca tablas de negocio.
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def check_migraciones() -> None:
    with get_engine().connect() as conn:
        verificar_migraciones(conn)


def check_redis() -> None:
    _redis_client().ping()


def _run(check: Callable[[], None]) -> str:
    try:
        check()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - cualquier fallo marca el servicio como caído
        logger.warning("Falla en chequeo de salud %s: %s", check.__name__, exc)
        return "error"


@router.get("/health")
def health() -> JSONResponse:
    # Se buscan las funciones en el módulo en cada llamada para poder sustituirlas en pruebas.
    checks = {
        "db": _run(check_db),
        "migraciones": _run(check_migraciones),
        "redis": _run(check_redis),
    }
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "error", "checks": checks},
    )
